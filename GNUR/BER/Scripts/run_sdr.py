"""
Varre ganhos de RF/IF do tx e de RF do rx (início fim passo cada um), rodando
tx_BER.py + rx_BER.py juntos e o comparador para cada combinação.
Salva um .csv com os resultados e gráficos (linha se só uma variável varia,
heatmap se duas ou três variam).

Uso (execute na mesma pasta de tx_BER.py, rx_BER.py, comparador.py e tx.txt):
    python3 run_sdr.py
    python3 run_sdr.py --tx-rf 0 14 14
    python3 run_sdr.py --tx-if 0 47 10
    python3 run_sdr.py --rx-rf 0 50 10
    python3 run_sdr.py --tx-rf 0 14 14 --tx-if 0 47 10 --rx-rf 0 50 10
    python3 run_sdr.py --verbose          # mostra erros/bits, IC e blocos de cada rodada

A saída do tx_BER.py e do rx_BER.py vai toda pro arquivo flowgraph.log (não
aparece no console). Log, .csv e gráficos são salvos dentro da pasta "Saídas/".
"""
import argparse
import csv
import itertools
import os
import re
import subprocess
import sys
import time

import matplotlib
matplotlib.use("Agg")  # só salva os PNG; funciona também sem display
import matplotlib.pyplot as plt

from comparador import SYNC  # palavra de sincronismo do tx.txt (mesma do gerar_tx.py)

TX_SCRIPT = "tx_BER.py"
RX_SCRIPT = "rx_BER.py"
COMPARADOR = "comparador.py"
TX, RX = "tx.txt", "tx_hat.txt"
BIT_RATE = 800e3     # igual ao "bit_rate" hardcoded nos dois flowgraphs
OUTDIR = "Saídas"    # log, csv e gráficos vão todos pra cá
# Ganhos aceitos pelo R820T (saída do rtl_test)
RX_GANHOS = [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9,
             25.4, 28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9,
             44.5, 48.0, 49.6]
# HackRF (tx): amp de RF liga/desliga (0 ou 14 dB); VGA/IF de 0 a 47 dB em passos de 1 dB
TX_RF_GANHOS = [0.0, 14.0]
TX_IF_GANHOS = [float(i) for i in range(48)]
META_BER = 1e-5      # linha de referência no gráfico (0 desliga)
MIN_ERROS = 100      # abaixo disso (e > 0) a BER é considerada pouco confiável

# Linhas impressas pelo comparador.py
RE_BER = re.compile(r"BER payload:\s*(\S+)\s+\((\d+) bits errados / (\d+)\)")
RE_IC = re.compile(r"IC 95%:\s*\[([^,\]]+),\s*([^\]]+)\]")
RE_BLOCOS = re.compile(r"Blocos:\s*(\d+) medidos \| (\d+) com slip de bytes \| "
                       r"(\d+) perdidos/indeterminados\s+\(de (\d+)\)")

DIMS = ["tx_rf_gain", "tx_if_gain", "rx_rf_gain"]
CAMPOS = DIMS + ["ber", "ber_pct", "erros", "bits", "ic_inf", "ic_sup",
                 "blocos_medidos", "blocos_slip", "blocos_perdidos",
                 "blocos_total", "status"]


def build_range(start, end, step):
    """Lista de valores de start até end (inclusive) de step em step.
    step <= 0 ou start == end: fixa em um único valor (start)."""
    if step <= 0 or start == end:
        return [start]
    n = int(round((end - start) / step)) + 1
    return [round(start + i * step, 10) for i in range(n)]


def ajustar_ganhos(vals, validos):
    """Troca cada valor pelo ganho válido mais próximo (sem repetir)."""
    out = []
    for v in vals:
        g = min(validos, key=lambda x: abs(x - v))
        if g not in out:
            out.append(g)
    return out


def tempo_transmissao(margem, bit_rate):
    """Estima quantos segundos leva pra transmitir tx.txt inteiro, com margem de sobra."""
    return os.path.getsize(TX) * 8 / bit_rate + margem


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
    """Roda uma rodada. Devolve o código de saída de tx/rx se algum deles morreu
    antes da hora (None = ainda rodando, que é o normal)."""
    info = {}
    rx = iniciar(RX_SCRIPT, ["-g", str(rx_rf)], log)
    tx = None
    try:
        time.sleep(2)      # dá tempo do RX estabilizar (AGC/symbol sync) antes do TX começar
        tx = iniciar(TX_SCRIPT, ["-g", str(tx_rf), "-i", str(tx_if)], log)
        time.sleep(duracao)
        info["tx"], info["rx"] = tx.poll(), rx.poll()
    finally:               # mesmo com Ctrl+C, não deixa SDR/processo preso
        if tx is not None:
            parar(tx)
        parar(rx)
    return info


def diagnostico(info):
    """Avisos sobre tx/rx que terminaram sozinhos (em geral: SDR não encontrado, erro no script)."""
    msgs = []
    for nome in ("tx", "rx"):
        if info.get(nome) is not None:
            msgs.append(f"{nome} terminou antes da hora (código {info[nome]})")
    return msgs


def verificar_ambiente():
    """Falha cedo, com mensagem clara, se faltar algo ou se o tx.txt for do formato antigo."""
    faltando = [a for a in (TX, TX_SCRIPT, RX_SCRIPT, COMPARADOR) if not os.path.exists(a)]
    if faltando:
        sys.exit(f"Arquivo(s) não encontrado(s) nesta pasta: {', '.join(faltando)}")
    with open(TX, "rb") as f:
        if SYNC.tobytes() not in f.read():
            sys.exit(f"{TX} não está no formato novo (SYNC não encontrado). "
                     "Gere com: python gerar_tx.py  (e transmita esse arquivo).")


def medir():
    """Roda o comparador e devolve um dict com BER, nº de erros/bits, IC 95%
    e contagem de blocos. `ber` é None se não houve medida válida."""
    r = dict(ber=None, ber_pct=None, erros=None, bits=None, ic_inf=None, ic_sup=None,
             blocos_medidos=None, blocos_slip=None, blocos_perdidos=None,
             blocos_total=None, status="ok")
    if not os.path.exists(RX):
        r["status"] = f"rx não gerou {RX}"
        return r
    p = subprocess.run([sys.executable, COMPARADOR, TX, RX],
                       capture_output=True, text=True)
    out = p.stdout

    m = RE_BER.search(out)
    if not m or int(m.group(3)) == 0:
        linhas = p.stderr.strip().splitlines()
        r["status"] = linhas[-1] if linhas else "sem BER na saída do comparador"
        return r
    r["ber"], r["erros"], r["bits"] = float(m.group(1)), int(m.group(2)), int(m.group(3))
    r["ber_pct"] = r["ber"] * 100

    m = RE_IC.search(out)
    if m:
        r["ic_inf"], r["ic_sup"] = float(m.group(1)), float(m.group(2))
    m = RE_BLOCOS.search(out)
    if m:
        (r["blocos_medidos"], r["blocos_slip"],
         r["blocos_perdidos"], r["blocos_total"]) = map(int, m.groups())

    avisos = []
    if 0 < r["erros"] < MIN_ERROS:  # 0 erros não é "incerto": vira limite superior
        avisos.append(f"poucos erros ({r['erros']})")
    if r["blocos_total"] and r["blocos_medidos"] < 0.9 * r["blocos_total"]:
        avisos.append("mais de 10% dos blocos não medidos")
    if avisos:
        r["status"] = "; ".join(avisos)
    return r


def linha_ber(r, verbose):
    """Texto da linha de resultado de uma rodada."""
    if r["ber"] is None:
        return f"  BER: não medida ({r['status']})"
    if r["erros"] == 0:
        txt = f"  BER = 0  (0 erros; BER < {r['ic_sup']:.1e} = {r['ic_sup'] * 100:.2g}% a 95%)"
    else:
        txt = f"  BER = {r['ber']:.3e}  ({r['ber_pct']:.4g}%)"
    if verbose:
        txt += (f"  ({r['erros']} erros / {r['bits']} bits)"
                f"  IC95% [{r['ic_inf']:.1e}, {r['ic_sup']:.1e}]"
                f"  blocos medidos {r['blocos_medidos']}/{r['blocos_total']}"
                f"  (slip {r['blocos_slip']}, perdidos {r['blocos_perdidos']})")
    if r["status"] != "ok":
        txt += f"  ⚠ {r['status']}" if verbose else "  ⚠"
    return txt


def salvar_csv(resultados, caminho, verbose):
    with open(caminho, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        for r in resultados:
            w.writerow({k: r[k] for k in CAMPOS})
    if verbose:
        print(f"CSV salvo em {caminho}")


def plotar_tudo(resultados, dims, out, meta=META_BER):
    """Gráfico de linhas: BER (log) x ganho do rx, uma curva por ganho de IF do tx
    e um painel por ganho de RF do tx. Se algum desses não variar, o papel passa
    para a próxima variável que varia. Barras = IC 95%; 'v' = 0 erros (limite
    superior); '×' = sem medida; tracejado = meta."""
    var = {nome: vals for nome, vals in dims if len(vals) > 1}
    fixas = {nome: vals[0] for nome, vals in dims if len(vals) == 1}
    validos = [r for r in resultados if r["ber"] is not None]
    if not validos:
        print("Nenhuma BER válida para plotar.")
        return []

    x = next((n for n in ("rx_rf_gain", "tx_if_gain", "tx_rf_gain") if n in var),
             "rx_rf_gain")
    resto = [n for n in ("tx_if_gain", "tx_rf_gain") if n in var and n != x]
    curva = resto[0] if resto else None
    painel = resto[1] if len(resto) > 1 else None

    vals_x = dict(dims)[x]
    vals_curva = dict(dims)[curva] if curva else [None]
    vals_painel = dict(dims)[painel] if painel else [None]
    cores = {v: plt.cm.viridis(i / max(len(vals_curva) - 1, 1) * 0.9)
             for i, v in enumerate(vals_curva)}

    menor = min((r["ic_sup"] if r["erros"] == 0 else r["ber"]) for r in validos)
    ymin = min(menor / 3, meta / 3 if meta else menor)

    fig, eixos = plt.subplots(1, len(vals_painel), sharey=True, squeeze=False,
                              figsize=(6.5 * len(vals_painel) + 1.5, 5))
    for ax, vp in zip(eixos[0], vals_painel):
        for ic, vc in enumerate(vals_curva):
            rs = sorted((r for r in resultados
                         if (painel is None or r[painel] == vp)
                         and (curva is None or r[curva] == vc)), key=lambda r: r[x])
            ok = [r for r in rs if r["ber"] is not None]
            falha = [r for r in rs if r["ber"] is None]
            cor = cores[vc]
            # desloca levemente cada curva no eixo x para os pontos não se esconderem
            dx = (ic - (len(vals_curva) - 1) / 2) * 0.012 * (max(vals_x) - min(vals_x))
            if ok:
                zero = [r["erros"] == 0 for r in ok]
                xs = [r[x] + dx for r in ok]
                ys = [r["ic_sup"] if z else r["ber"] for r, z in zip(ok, zero)]
                inf = [0 if z else max(r["ber"] - r["ic_inf"], 0) for r, z in zip(ok, zero)]
                sup = [0 if z else max(r["ic_sup"] - r["ber"], 0) for r, z in zip(ok, zero)]
                ax.errorbar(xs, ys, yerr=[inf, sup], fmt="o-", color=cor, capsize=3,
                            ms=5, lw=1.5, label=None if curva is None else f"{vc:g}")
                zx = [a for a, z in zip(xs, zero) if z]
                zy = [b for b, z in zip(ys, zero) if z]
                if zx:
                    ax.plot(zx, zy, "v", color=cor, ms=10, mfc="white", mew=1.5)
            if falha:
                ax.plot([r[x] + dx for r in falha], [1] * len(falha), "x", color=cor, ms=9, mew=2)
        if meta:
            ax.axhline(meta, color="gray", ls="--", lw=1)
        ax.set_yscale("log")
        ax.set_ylim(ymin, 1.5)
        ax.set_xlabel(x)
        if len(vals_x) <= 15:
            ax.set_xticks(vals_x)
        ax.grid(True, which="both", alpha=0.3)
        if painel:
            ax.set_title(f"{painel} = {vp:g}")
    eixos[0][0].set_ylabel("BER")
    if curva:
        eixos[0][-1].legend(title=curva, loc="upper left", bbox_to_anchor=(1.02, 1), fontsize=8)

    titulo = "BER x " + x
    if fixas:
        titulo += "   (" + ", ".join(f"{k}={v:g}" for k, v in fixas.items()) + ")"
    fig.suptitle(titulo)
    fig.text(0.5, 0.005, "barras: IC 95%   |   v: 0 erros (limite superior)   |   ×: sem medida"
             + (f"   |   tracejado: {meta:g}" if meta else ""), ha="center", fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 0.96))
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"Gráfico salvo em {out}")
    return [out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tx-rf", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                    default=[0.0, 0.0, 0.0], help="ganho de RF do tx (hackrf): só 0 ou 14")
    ap.add_argument("--tx-if", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                    default=[30.0, 30.0, 0.0], help="ganho de IF do tx (hackrf): 0 a 47")
    ap.add_argument("--rx-rf", nargs=3, type=float, metavar=("INICIO", "FIM", "PASSO"),
                    default=[0.0, 0.0, 0.0], help="ganho de RF do rx (rtl-sdr); ajustado ao valor válido mais próximo")
    ap.add_argument("--margem", type=float, default=2.0,
                    help="segundos extras além do tempo estimado de transmissão")
    ap.add_argument("--bit-rate", type=float, default=BIT_RATE,
                    help="bit rate usado para estimar a duração de cada rodada")
    ap.add_argument("--out", default="ber_sweep_rf.png",
                    help=f"nome do arquivo de gráfico (salvo dentro de {OUTDIR}/)")
    ap.add_argument("--csv", default="ber_sweep_rf.csv",
                    help=f"nome do arquivo csv (salvo dentro de {OUTDIR}/)")
    ap.add_argument("--meta", type=float, default=META_BER,
                    help="BER de referência desenhada no gráfico (0 desliga; padrão 1e-5)")
    ap.add_argument("--verbose", action="store_true",
                    help="mostra mais detalhes: erros/bits, IC, blocos e caminhos de saída")
    args = ap.parse_args()

    verificar_ambiente()
    os.makedirs(OUTDIR, exist_ok=True)
    log_path = os.path.join(OUTDIR, "flowgraph.log")
    out_path = os.path.join(OUTDIR, os.path.basename(args.out))
    csv_path = os.path.join(OUTDIR, os.path.basename(args.csv))

    dims = []
    for nome, rng, validos in (("tx_rf_gain", args.tx_rf, TX_RF_GANHOS),
                               ("tx_if_gain", args.tx_if, TX_IF_GANHOS),
                               ("rx_rf_gain", args.rx_rf, RX_GANHOS)):
        pedido = build_range(*rng)
        vals = ajustar_ganhos(pedido, validos)
        if vals != pedido:
            print(f"{nome} ajustado aos ganhos válidos: {vals}")
        dims.append((nome, vals))

    duracao = tempo_transmissao(args.margem, args.bit_rate)
    print(f"tx.txt: {os.path.getsize(TX)} B  ->  ~{duracao:.1f} s por rodada "
          f"(a {args.bit_rate / 1e3:.0f} kbit/s + {args.margem:.1f} s de margem)")
    if args.verbose:
        print(f"Saída dos flowgraphs vai para {log_path}")

    resultados = []
    with open(log_path, "a") as log:
        for trf, tif, rrf in itertools.product(*(vals for _, vals in dims)):
            print(f"tx_rf={trf}  tx_if={tif}  rx_rf={rrf} ...", flush=True)

            # apaga o recebido antigo: se o rx falhar, não medimos lixo da rodada anterior
            if os.path.exists(RX):
                os.remove(RX)
            info = rodar_ponto(trf, tif, rrf, duracao, log)
            log.flush()

            r = medir()
            extra = diagnostico(info)
            if extra:
                r["status"] = "; ".join(extra) if r["status"] == "ok" else r["status"] + "; " + "; ".join(extra)
            r.update(tx_rf_gain=trf, tx_if_gain=tif, rx_rf_gain=rrf)
            resultados.append(r)
            print(linha_ber(r, args.verbose), flush=True)

    salvar_csv(resultados, csv_path, args.verbose)
    poucos = sum(1 for r in resultados if r["ber"] is not None and 0 < r["erros"] < MIN_ERROS)
    if poucos:
        print(f"⚠ {poucos} ponto(s) com menos de {MIN_ERROS} erros (BER incerta).")
    plotar_tudo(resultados, dims, out_path, args.meta)


if __name__ == "__main__":
    main()
