"""
Varre noise_voltage, freq_offset e epsilon (cada um como início fim passo),
rodando o flowgraph e o comparador para cada combinação, e plota BER no final.

Uso (execute na mesma pasta de qpsk_loopback_5g.py, comparador_min.py e tx.txt):
    python run.py
    python run.py --noise 0 4 0.5
    python run.py --freq -0.0002 0.0002 0.000001
    python run.py --eps 0.99 1.01 0.0001
    python run.py --noise 0 10 0.01 --freq -0.0002 0.0002 0.000001 --eps 0.99 1.01 0.0001
"""
import argparse
import itertools
import re
import subprocess
import sys

import matplotlib.pyplot as plt

FLOWGRAPH = "loopback.py"
COMPARADOR = "comparador.py"
TX, RX = "tx.txt", "tx_hat.txt"


def build_range(start, end, step):
    """Lista de valores de start até end (inclusive) de step em step.
    step <= 0 ou start == end: fixa em um único valor (start)."""
    if step <= 0 or start == end:
        return [start]
    n = int(round((end - start) / step)) + 1
    return [round(start + i * step, 10) for i in range(n)]


def run_flowgraph(noise, freq_offset, epsilon, timeout=None):
    """Roda o flowgraph e espera ele terminar sozinho (tx.txt é finito).
    Se `timeout` for passado, mata o processo caso ele não termine a tempo."""
    cmd = [sys.executable, FLOWGRAPH,
           "-n", str(noise), "-f", str(freq_offset), "-e", str(epsilon)]
    proc = subprocess.Popen(cmd)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()          # aciona o sig_handler (SIGTERM) do flowgraph
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def medir_ber():
    """Roda o comparador e extrai o BER da saída."""
    saida = subprocess.run(
        [sys.executable, COMPARADOR, TX, RX],
        capture_output=True, text=True,
    ).stdout
    m = re.search(r"BER payload:\s*([\d.eE+-]+)", saida)
    return float(m.group(1)) if m else None


def plotar(resultados, dims, out):
    """resultados: lista de (noise, freq, eps, ber). dims: [(nome, valores), ...].
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
    ap.add_argument("--noise", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                     default=[0.0, 0.0, 0.0])
    ap.add_argument("--freq", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                     default=[0.0, 0.0, 0.0])
    ap.add_argument("--eps", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                     default=[1.0, 1.0, 0.0])
    ap.add_argument("--timeout", type=float, default=None,
                     help="segundos máximos esperando cada rodada (padrão: sem limite, "
                          "espera o flowgraph terminar sozinho)")
    ap.add_argument("--out", default="ber_sweep.png")
    args = ap.parse_args()

    noise_vals = build_range(*args.noise)
    freq_vals = build_range(*args.freq)
    eps_vals = build_range(*args.eps)
    dims = [("noise_voltage", noise_vals), ("freq_offset", freq_vals), ("epsilon", eps_vals)]

    resultados = []
    for n, f, e in itertools.product(noise_vals, freq_vals, eps_vals):
        print(f"noise={n}  freq_offset={f}  epsilon={e} ...", flush=True)
        run_flowgraph(n, f, e, args.timeout)
        ber = medir_ber()
        print(f"  BER = {ber:.3e}" if ber is not None else "  BER: não detectado")
        resultados.append((n, f, e, ber))

    plotar(resultados, dims, args.out)


if __name__ == "__main__":
    main()