"""
Varre ganhos de RF/IF do tx e de RF do rx (início fim passo cada um), rodando
tx_BER_script.py + rx_BER_script.py juntos e o comparador para cada combinação,
e plota BER no final.

Uso (execute na mesma pasta de tx_BER_script.py, rx_BER_script.py,
comparador_min.py e tx.txt):
    python3 run_sweep_rf.py
    python3 run_sweep_rf.py --tx-rf 0 20 5
    python3 run_sweep_rf.py --tx-rf 0 20 5 --rx-rf 0 30 10
"""
import argparse
import itertools
import os
import re
import subprocess
import sys
import time

import matplotlib.pyplot as plt

TX_SCRIPT = "tx_BER.py"
RX_SCRIPT = "rx_BER.py"
COMPARADOR = "comparador.py"
TX, RX = "tx.txt", "tx_hat.txt"
BIT_RATE = 800e3     # igual ao "bit_rate" hardcoded nos dois flowgraphs


def build_range(start, end, step):
    """Lista de valores de start até end (inclusive) de step em step.
    step <= 0 ou start == end: fixa em um único valor (start)."""
    if step <= 0 or start == end:
        return [start]
    n = int(round((end - start) / step)) + 1
    return [round(start + i * step, 10) for i in range(n)]


def tempo_transmissao(margem):
    """Estima quantos segundos leva pra transmitir tx.txt inteiro, com margem de sobra."""
    tamanho_bits = os.path.getsize(TX) * 8
    return tamanho_bits / BIT_RATE + margem


def iniciar(script, args):
    """Sobe o flowgraph como subprocesso, com stdin aberto (ele fica esperando
    'Enter' pra sair — não fechamos o stdin pra não disparar esse Enter cedo)."""
    return subprocess.Popen([sys.executable, script] + args, stdin=subprocess.PIPE)


def parar(proc):
    """Manda um Enter pro processo (aciona o 'Press Enter to quit') e espera terminar."""
    try:
        proc.communicate(input=b"\n", timeout=15)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def rodar_ponto(tx_rf, tx_if, rx_rf, duracao):
    rx = iniciar(RX_SCRIPT, ["-g", str(rx_rf)])
    time.sleep(2)      # dá tempo do RX estabilizar (AGC/symbol sync) antes do TX começar
    tx = iniciar(TX_SCRIPT, ["-g", str(tx_rf), "-i", str(tx_if)])
    time.sleep(duracao)
    parar(tx)
    parar(rx)


def medir_ber():
    """Roda o comparador e extrai o BER da saída."""
    saida = subprocess.run(
        [sys.executable, COMPARADOR, TX, RX],
        capture_output=True, text=True,
    ).stdout
    m = re.search(r"BER payload:\s*([\d.eE+-]+)", saida)
    return float(m.group(1)) if m else None


def plotar(resultados, dims, out):
    """resultados: lista de (tx_rf, tx_if, rx_rf, ber). dims: [(nome, valores), ...].
    Plota BER contra a primeira variável que tiver mais de um valor; se uma
    segunda também variar, desenha uma linha por combinação dela."""
    variando = [(nome, vals) for nome, vals in dims if len(vals) > 1]

    plt.figure()
    if len(variando) <= 1:
        eixo_x = variando[0][0] if variando else dims[0][0]
        idx = [d[0] for d in dims].index(eixo_x)
        xs = [r[idx] for r in resultados if r[3] is not None]
        ys = [r[3] if r[3] > 0 else 1e-6 for r in resultados if r[3] is not None]
        plt.semilogy(xs, ys, "o-")
    else:
        eixo_x, outro = variando[0][0], variando[1][0]
        ix, io = [d[0] for d in dims].index(eixo_x), [d[0] for d in dims].index(outro)
        for val_outro in dims[io][1]:
            xs = [r[ix] for r in resultados if r[io] == val_outro and r[3] is not None]
            ys = [r[3] if r[3] > 0 else 1e-6
                  for r in resultados if r[io] == val_outro and r[3] is not None]
            plt.semilogy(xs, ys, "o-", label=f"{outro}={val_outro}")
        plt.legend()

    plt.xlabel(eixo_x)
    plt.ylabel("BER")
    plt.title("BER x " + eixo_x)
    plt.grid(True, which="both")
    plt.savefig(out, dpi=150)
    print(f"Gráfico salvo em {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx-rf", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                     default=[0.0, 0.0, 0.0], help="ganho de RF do tx (hackrf)")
    ap.add_argument("--tx-if", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                     default=[30.0, 30.0, 0.0], help="ganho de IF do tx (hackrf)")
    ap.add_argument("--rx-rf", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                     default=[0.0, 0.0, 0.0], help="ganho de RF do rx (rtl-sdr)")
    ap.add_argument("--margem", type=float, default=2.0,
                     help="segundos extras além do tempo estimado de transmissão")
    ap.add_argument("--out", default="ber_sweep_rf.png")
    args = ap.parse_args()

    tx_rf_vals = build_range(*args.tx_rf)
    tx_if_vals = build_range(*args.tx_if)
    rx_rf_vals = build_range(*args.rx_rf)
    dims = [("tx_rf_gain", tx_rf_vals), ("tx_if_gain", tx_if_vals), ("rx_rf_gain", rx_rf_vals)]

    duracao = tempo_transmissao(args.margem)
    print(f"tx.txt: {os.path.getsize(TX)} B  ->  ~{duracao:.1f} s por rodada "
          f"(a {BIT_RATE/1e3:.0f} kbit/s + {args.margem:.1f} s de margem)")

    resultados = []
    for trf, tif, rrf in itertools.product(tx_rf_vals, tx_if_vals, rx_rf_vals):
        print(f"tx_rf={trf}  tx_if={tif}  rx_rf={rrf} ...", flush=True)
        rodar_ponto(trf, tif, rrf, duracao)
        ber = medir_ber()
        print(f"  BER = {ber:.3e}" if ber is not None else "  BER: não detectado")
        resultados.append((trf, tif, rrf, ber))

    plotar(resultados, dims, args.out)


if __name__ == "__main__":
    main()