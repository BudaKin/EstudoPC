"""
Varre noise_voltage, freq_offset e epsilon (cada um como início fim passo),
rodando o flowgraph e o comparador para cada combinação, e plota BER no final.

Uso (execute na mesma pasta de loopback.py, comparador.py, gerar_tx.py e tx.txt):
    python run.py
    python run.py --noise 0 4 0.5
    python run.py --freq -0.0002 0.0002 0.000001
    python run.py --eps 0.99 1.01 0.0001
    python run.py --noise 0 10 0.01 --freq -0.0002 0.0002 0.000001 --eps 0.99 1.01 0.0001

Saídas:
    ber_sweep.png  gráfico BER (com intervalo de confiança de 95%)
    ber_sweep.csv  todos os campos do comparador por rodada (gravado a cada rodada)
"""
import argparse
import csv
import itertools
import os
import re
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")  # só salva o PNG; funciona também sem display
import matplotlib.pyplot as plt

FLOWGRAPH = "loopback.py"
COMPARADOR = "comparador.py"
TX, RX = "tx.txt", "tx_hat.txt"
MIN_ERROS = 100  # abaixo disso a BER é considerada pouco confiável

# Linhas impressas pelo comparador.py
RE_BER = re.compile(r"BER payload:\s*(\S+)\s+\((\d+) bits errados / (\d+)\)")
RE_IC = re.compile(r"IC 95%:\s*\[([^,\]]+),\s*([^\]]+)\]")
RE_BLOCOS = re.compile(r"Blocos:\s*(\d+) medidos \| (\d+) com slip de bytes \| "
                       r"(\d+) perdidos/indeterminados\s+\(de (\d+)\)")

CAMPOS = ["noise_voltage", "freq_offset", "epsilon", "ber", "ber_pct", "erros", "bits",
          "ic_inf", "ic_sup", "blocos_medidos", "blocos_slip", "blocos_perdidos",
          "blocos_total", "status"]


def build_range(start, end, step):
    """Lista de valores de start até end (inclusive) de step em step.
    step <= 0 ou start == end: fixa em um único valor (start)."""
    if step <= 0 or start == end:
        return [start]
    n = int(round((end - start) / step)) + 1
    return [round(start + i * step, 10) for i in range(n)]


def run_flowgraph(noise, freq_offset, epsilon, timeout=None, verbose=False):
    """Roda o flowgraph e espera ele terminar sozinho (tx.txt é finito).
    Se `timeout` for passado, mata o processo caso ele não termine a tempo.
    Devolve True se terminou normalmente (código de saída 0)."""
    cmd = [sys.executable, FLOWGRAPH,
           "-n", str(noise), "-f", str(freq_offset), "-e", str(epsilon)]
    # por padrão esconde a saída do flowgraph (use --verbose para ver)
    proc = subprocess.Popen(cmd, stdout=None if verbose else subprocess.DEVNULL)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()          # aciona o sig_handler (SIGTERM) do flowgraph
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return False
    return proc.returncode == 0


def medir():
    """Roda o comparador e devolve um dict com BER, nº de erros/bits, IC 95%
    e contagem de blocos. `ber` é None se não houve medida válida."""
    r = dict(ber=None, erros=None, bits=None, ic_inf=None, ic_sup=None,
             blocos_medidos=None, blocos_slip=None, blocos_perdidos=None,
             blocos_total=None, status="ok")
    p = subprocess.run([sys.executable, COMPARADOR, TX, RX],
                       capture_output=True, text=True)
    out = p.stdout

    m = RE_BER.search(out)
    if not m or int(m.group(3)) == 0:
        linhas = p.stderr.strip().splitlines()
        r["status"] = linhas[-1] if linhas else "sem BER na saída do comparador"
        return r
    r["ber"], r["erros"], r["bits"] = float(m.group(1)), int(m.group(2)), int(m.group(3))

    m = RE_IC.search(out)
    if m:
        r["ic_inf"], r["ic_sup"] = float(m.group(1)), float(m.group(2))
    m = RE_BLOCOS.search(out)
    if m:
        (r["blocos_medidos"], r["blocos_slip"],
         r["blocos_perdidos"], r["blocos_total"]) = map(int, m.groups())

    avisos = []
    if r["erros"] < MIN_ERROS:
        avisos.append(f"poucos erros ({r['erros']})")
    if r["blocos_total"] and r["blocos_medidos"] < 0.9 * r["blocos_total"]:
        avisos.append("mais de 10% dos blocos não medidos")
    if avisos:
        r["status"] = "; ".join(avisos)
    return r


def plotar(resultados, dims, out):
    """resultados: lista de dicts (ver CAMPOS). dims: [(nome, valores), ...].
    O eixo x é a primeira variável que varia; as outras que variam viram uma
    curva cada. Barras de erro = IC 95%. Pontos com 0 erros aparecem como 'v'
    no limite superior do IC (a BER é no máximo isso, não zero)."""
    nomes = [d[0] for d in dims]
    variando = [n for n, vals in dims if len(vals) > 1]
    eixo_x = variando[0] if variando else nomes[0]
    outros = variando[1:]

    validos = [r for r in resultados if r["ber"] is not None]
    if not validos:
        print("Nenhuma BER válida para plotar.")
        return

    series = {}
    for r in validos:
        chave = tuple(r[n] for n in outros)
        series.setdefault(chave, []).append(r)

    plt.figure()
    for chave, rs in series.items():
        rs.sort(key=lambda r: r[eixo_x])
        xs = [r[eixo_x] for r in rs]
        zero = [r["erros"] == 0 for r in rs]
        ys = [r["ic_sup"] if z else r["ber"] for r, z in zip(rs, zero)]
        inf = [0 if z else max(r["ber"] - r["ic_inf"], 0) for r, z in zip(rs, zero)]
        sup = [0 if z else max(r["ic_sup"] - r["ber"], 0) for r, z in zip(rs, zero)]
        label = ", ".join(f"{n}={v}" for n, v in zip(outros, chave)) or None
        linha = plt.errorbar(xs, ys, yerr=[inf, sup], fmt="o-", capsize=3, label=label)
        cor = linha[0].get_color()
        zx = [x for x, z in zip(xs, zero) if z]
        zy = [y for y, z in zip(ys, zero) if z]
        if zx:
            plt.plot(zx, zy, "v", color=cor, markersize=9, mfc="none")
    plt.yscale("log")
    if outros:
        plt.legend()
    plt.xlabel(eixo_x)
    plt.ylabel("BER")
    plt.title("BER x " + eixo_x + "  (barras: IC 95%; v = 0 erros, limite superior)")
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
    ap.add_argument("--verbose", action="store_true",
                    help="mostra também a saída do flowgraph")
    args = ap.parse_args()

    noise_vals = build_range(*args.noise)
    freq_vals = build_range(*args.freq)
    eps_vals = build_range(*args.eps)
    dims = [("noise_voltage", noise_vals), ("freq_offset", freq_vals), ("epsilon", eps_vals)]
    csv_path = os.path.splitext(args.out)[0] + ".csv"

    resultados = []
    with open(csv_path, "w", newline="") as fcsv:
        w = csv.DictWriter(fcsv, fieldnames=CAMPOS)
        w.writeheader()

        for n, f, e in itertools.product(noise_vals, freq_vals, eps_vals):

            # apaga o recebido antigo: se o flowgraph falhar, não medimos lixo da rodada anterior
            if os.path.exists(RX):
                os.remove(RX)
            terminou = run_flowgraph(n, f, e, args.timeout, args.verbose)

            if os.path.exists(RX):
                r = medir()
                if not terminou and r["status"] == "ok":
                    r["status"] = "flowgraph não terminou normalmente (timeout/erro)"
            else:
                r = dict(ber=None, erros=None, bits=None, ic_inf=None, ic_sup=None,
                         blocos_medidos=None, blocos_slip=None, blocos_perdidos=None,
                         blocos_total=None, status=f"flowgraph não gerou {RX}")

            r.update(noise_voltage=n, freq_offset=f, epsilon=e,
                     ber_pct=None if r["ber"] is None else r["ber"] * 100)
            resultados.append(r)
            w.writerow({k: r[k] for k in CAMPOS})
            fcsv.flush()

            param = f"noise={n} freq={f} eps={e}"
            if r["ber"] is None:
                print(f"{param}  ->  BER não medida  ⚠ {r['status']}")
            else:
                print(f"{param}  ->  BER = {r['ber']:.3e}  ({r['ber_pct']:.4g}%)"
                      + ("" if r["status"] == "ok" else f"  ⚠ {r['status']}"), flush=True)

    print(f"Resultados salvos em {csv_path}")
    poucos = sum(1 for r in resultados if r["ber"] is not None and r["erros"] < MIN_ERROS)
    if poucos:
        print(f"⚠ {poucos} ponto(s) com menos de {MIN_ERROS} erros (BER incerta).")
    plotar(resultados, dims, args.out)


if __name__ == "__main__":
    main()