"""
Varre ganhos de RF/IF do tx e de RF do rx (início fim passo cada um), rodando
tx_BER_script.py + rx_BER_script.py juntos e o comparador para cada combinação.
Salva um .csv com os resultados e um gráfico por combinação das variáveis
secundárias (se só uma variável varia, sai um gráfico só).

Uso (execute na mesma pasta de tx_BER.py, rx_BER.py,
comparador.py e tx.txt):
    python3 run_sdr.py
    python3 run_sdr.py --tx-rf 0 30 30
    python3 run_sdr.py --tx-if 0 50 10
    python3 run_sdr.py --rx-rf 0 50 10
    python3 run_sdr.py --tx-rf 0 30 30 --tx-if 0 50 10 --rx-rf 0 50 10

A saída do tx_BER_script.py e do rx_BER_script.py vai toda pro arquivo
flowgraph.log (não aparece no console) — só o BER de cada rodada é mostrado.
Log, .csv e gráficos são salvos dentro da pasta "Saídas/".
"""
import argparse
import csv
import itertools
import os
import re
import subprocess
import sys
import time

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm

TX_SCRIPT = "tx_BER.py"
RX_SCRIPT = "rx_BER.py"
COMPARADOR = "comparador.py"
TX, RX = "tx.txt", "tx_hat.txt"
BIT_RATE = 800e3     # igual ao "bit_rate" hardcoded nos dois flowgraphs
OUTDIR = "Saídas"    # log, csv e gráficos vão todos pra cá


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


def iniciar(script, args, log):
    """Sobe o flowgraph como subprocesso, com stdin aberto (ele fica esperando
    'Enter' pra sair — não fechamos o stdin pra não disparar esse Enter cedo).
    stdout/stderr vão pro arquivo de log, não pro console."""
    return subprocess.Popen([sys.executable, script] + args,
                             stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)


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


def rodar_ponto(tx_rf, tx_if, rx_rf, duracao, log):
    rx = iniciar(RX_SCRIPT, ["-g", str(rx_rf)], log)
    time.sleep(2)      # dá tempo do RX estabilizar (AGC/symbol sync) antes do TX começar
    tx = iniciar(TX_SCRIPT, ["-g", str(tx_rf), "-i", str(tx_if)], log)
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


def salvar_csv(resultados, dims, caminho):
    with open(caminho, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([nome for nome, _ in dims] + ["ber"])
        w.writerows(resultados)
    print(f"CSV salvo em {caminho}")


def plotar_tudo(resultados, dims, out):
    """resultados: lista de (tx_rf, tx_if, rx_rf, ber). dims: [(nome, valores), ...].
    1 variável variando: gráfico de linha simples (BER x essa variável).
    2 ou 3 variáveis variando: heatmap das duas primeiras (BER como cor, com o
    valor exato escrito em cada célula); se uma terceira também variar, gera um
    heatmap por valor dela, em vez de um gráfico por combinação (bem menos arquivos)."""
    variando = [(nome, vals) for nome, vals in dims if len(vals) > 1]
    idx = {nome: i for i, (nome, _) in enumerate(dims)}
    base, ext = os.path.splitext(out)
    arquivos = []

    if len(variando) <= 1:
        eixo_x, _ = variando[0] if variando else dims[0]
        pontos = [r for r in resultados if r[3] is not None]
        xs = [r[idx[eixo_x]] for r in pontos]
        ys = [r[3] if r[3] > 0 else 1e-6 for r in pontos]

        plt.figure()
        plt.semilogy(xs, ys, "o-")
        plt.xlabel(eixo_x)
        plt.ylabel("BER")
        plt.title("BER x " + eixo_x)
        plt.grid(True, which="both")
        plt.savefig(out, dpi=150)
        plt.close()
        print(f"Gráfico salvo em {out}")
        return [out]

    eixo_x, vals_x = variando[0]
    eixo_y, vals_y = variando[1]
    outras = variando[2:]  # no máximo 1 variável extra (só existem 3 no total)
    combinacoes = list(itertools.product(*(vals for _, vals in outras))) if outras else [()]

    for combo in combinacoes:
        filtro = dict(zip([nome for nome, _ in outras], combo))
        matriz = np.full((len(vals_y), len(vals_x)), np.nan)
        for r in resultados:
            if all(r[idx[k]] == v for k, v in filtro.items()) and r[3] is not None:
                ix, iy = vals_x.index(r[idx[eixo_x]]), vals_y.index(r[idx[eixo_y]])
                matriz[iy, ix] = r[3]

        plt.figure(figsize=(1.3 * len(vals_x) + 2, 1.1 * len(vals_y) + 2))
        cores = np.where(matriz > 0, matriz, 1e-12)
        im = plt.imshow(cores, origin="lower", aspect="auto",
                         norm=LogNorm(vmin=max(np.nanmin(cores), 1e-6), vmax=np.nanmax(cores)))
        plt.xticks(range(len(vals_x)), vals_x)
        plt.yticks(range(len(vals_y)), vals_y)
        plt.xlabel(eixo_x)
        plt.ylabel(eixo_y)
        plt.colorbar(im, label="BER")
        for iy in range(len(vals_y)):
            for ix in range(len(vals_x)):
                v = matriz[iy, ix]
                texto = f"{v:.2e}" if not np.isnan(v) else "-"
                plt.text(ix, iy, texto, ha="center", va="center", fontsize=8,
                         bbox=dict(facecolor="white", alpha=0.7, edgecolor="none", pad=1))

        titulo = f"BER: {eixo_x} x {eixo_y}"
        if filtro:
            titulo += "  (" + ", ".join(f"{k}={v}" for k, v in filtro.items()) + ")"
        plt.title(titulo)
        plt.tight_layout()

        sufixo = "_".join(f"{k}={v}" for k, v in filtro.items())
        arquivo = f"{base}_{sufixo}{ext}" if sufixo else out
        plt.savefig(arquivo, dpi=150)
        plt.close()
        arquivos.append(arquivo)
        print(f"Gráfico salvo em {arquivo}")
    return arquivos


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
    ap.add_argument("--out", default="ber_sweep_rf.png",
                     help=f"nome do arquivo de gráfico (salvo dentro de {OUTDIR}/)")
    ap.add_argument("--csv", default="ber_sweep_rf.csv",
                     help=f"nome do arquivo csv (salvo dentro de {OUTDIR}/)")
    args = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    log_path = os.path.join(OUTDIR, "flowgraph.log")
    out_path = os.path.join(OUTDIR, os.path.basename(args.out))
    csv_path = os.path.join(OUTDIR, os.path.basename(args.csv))

    tx_rf_vals = build_range(*args.tx_rf)
    tx_if_vals = build_range(*args.tx_if)
    rx_rf_vals = build_range(*args.rx_rf)
    dims = [("tx_rf_gain", tx_rf_vals), ("tx_if_gain", tx_if_vals), ("rx_rf_gain", rx_rf_vals)]

    duracao = tempo_transmissao(args.margem)
    print(f"tx.txt: {os.path.getsize(TX)} B  ->  ~{duracao:.1f} s por rodada "
          f"(a {BIT_RATE/1e3:.0f} kbit/s + {args.margem:.1f} s de margem)")
    print(f"Saída dos flowgraphs vai para {log_path}")

    resultados = []
    with open(log_path, "a") as log:
        for trf, tif, rrf in itertools.product(tx_rf_vals, tx_if_vals, rx_rf_vals):
            print(f"tx_rf={trf}  tx_if={tif}  rx_rf={rrf} ...", flush=True)
            rodar_ponto(trf, tif, rrf, duracao, log)
            ber = medir_ber()
            print(f"  BER = {ber:.3e}" if ber is not None else "  BER: não detectado")
            resultados.append((trf, tif, rrf, ber))

    salvar_csv(resultados, dims, csv_path)
    plotar_tudo(resultados, dims, out_path)


if __name__ == "__main__":
    main()