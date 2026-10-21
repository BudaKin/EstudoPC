#!/usr/bin/env python3
"""Analisa CSVs de varredura de hardware (iftxgain, rftxgain, rf_gain, bler, vazao_bps...).

Gera um único tipo de gráfico: BLER x rf_gain com intervalo de confiança de Wilson (95%),
um painel por configuração de TX (rftxgain, iftxgain). Com vários CSVs, todos são
plotados nos mesmos painéis, um por cor, com o nome do CSV na legenda.

Saídas (em --saida):
  <prefixo>_ic.png        faixa completa de rf_gain
  <prefixo>_ic_zoom.png   só o intervalo em que o BLER realmente varia (detectado sozinho)
  (com --vazao, também <prefixo>_vazao.png e <prefixo>_vazao_zoom.png)
O prefixo é o nome do CSV (se for um só) ou 'comparacao' (se forem vários).

Exemplos:
  python analise.py resultados_hw_casa1910.csv
  python analise.py resultados_hw_casa1910.csv resultados_hw_casa18101.csv resultados_hw_casa18102.csv
  python analise.py resultados_hw_*.csv --faixa 0,10 --vazao
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

PISO = 1e-5  # BLER = 0 não aparece em escala log; plota como este piso
MARCAS = "o^sDvP*X"
ROTULOS = {}  # opcional: --rotulo CHAVE=TEXTO troca o nome na legenda


def rotulo(nome):
    """Nome na legenda: o do CSV, a menos que --rotulo tenha definido outro."""
    for chave, texto in ROTULOS.items():
        if nome.endswith(chave):
            return texto
    return nome


def wilson(k, n, z=1.96):
    """Intervalo de confiança de 95% (Wilson) para a proporção k/n."""
    p = k / n
    d = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / d
    m = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / d
    return np.clip(c - m, 0, 1), np.clip(c + m, 0, 1)


def resumo(df, limiar):
    """Uma linha por configuração de TX (iftxgain, rftxgain)."""
    linhas = []
    for (i, r), g in df.groupby(["iftxgain", "rftxgain"]):
        ok = g[g.bler < limiar]
        linhas.append({
            "iftxgain": i, "rftxgain": r,
            "pontos": len(g), "operacionais": len(ok),
            "rf_gain_min_ok": ok.rf_gain.min() if len(ok) else np.nan,
            "rf_gain_max_ok": ok.rf_gain.max() if len(ok) else np.nan,
            "bler_min": g.bler.min(),
            "vazao_max_bps": g.vazao_bps.max(),
        })
    return pd.DataFrame(linhas)


def intervalo_transicao(dfs):
    """Faixa de rf_gain em que o BLER sai do patamar de falha (~1) e chega ao de zero erros.

    Por curva, considera 'em transição' os pontos cujo IC de Wilson está acima do piso e
    abaixo de 99%, mais um ponto vizinho de cada lado; devolve a união entre todas as
    curvas (arquivos e configurações de TX). None se nenhuma curva transita.
    """
    lo, hi = np.inf, -np.inf
    for df in dfs.values():
        for _, g in df.groupby(["rftxgain", "iftxgain"]):
            g = g.sort_values("rf_gain")
            l, h = wilson(g.erros.values, g.quadros.values)
            idx = np.where((l > PISO) & (h < 0.99))[0]
            if not len(idx):
                continue
            x = g.rf_gain.values
            lo = min(lo, x[max(idx[0] - 1, 0)])
            hi = max(hi, x[min(idx[-1] + 1, len(x) - 1)])
    return (lo, hi) if lo < hi else None


def grafico_ic(dfs, limiar, metrica="bler", xlim=None, extra=""):
    """Painéis por configuração de TX; uma cor por CSV; faixa = IC 95% de Wilson."""
    cfgs = sorted({c for d in dfs.values() for c in d.groupby(["rftxgain", "iftxgain"]).groups})
    nc = min(5, len(cfgs))
    nl = -(-len(cfgs) // nc)
    fig, ax = plt.subplots(nl, nc, figsize=(max(3.2 * nc, 7.5), max(2.6 * nl, 5) + 0.5),
                           squeeze=False, sharex=True, sharey=True)
    nominal = {}  # taxa sem erros de cada CSV (vazão = nominal * (1 - BLER))
    for nome, df in dfs.items():
        ok = df.bler < 1
        nominal[nome] = (df.vazao_bps[ok] / (1 - df.bler[ok])).max() if ok.any() else np.nan

    for a, (r, i) in zip(ax.flat, cfgs):
        for k, (nome, df) in enumerate(dfs.items()):
            g = df[(df.rftxgain == r) & (df.iftxgain == i)].sort_values("rf_gain")
            if g.empty:
                continue
            lo, hi = wilson(g.erros.values, g.quadros.values)
            cor, marca = f"C{k}", MARCAS[k % len(MARCAS)]
            if metrica == "bler":
                a.fill_between(g.rf_gain, np.clip(lo, PISO, 1), np.clip(hi, PISO, 1),
                               color=cor, alpha=0.25)
                a.plot(g.rf_gain, g.bler.clip(lower=PISO), marker=marca, ms=3.5, color=cor)
            else:
                n = nominal[nome] / 1e3
                a.fill_between(g.rf_gain, n * (1 - hi), n * (1 - lo), color=cor, alpha=0.25)
                a.plot(g.rf_gain, g.vazao_bps / 1e3, marker=marca, ms=3.5, color=cor)
        if metrica == "bler":
            a.set_yscale("log")
            a.set_ylim(PISO / 2, 2)
            a.axhline(limiar, color="k", ls="--", lw=0.7)
        if xlim:
            a.set_xlim(*xlim)
        a.set_title(f"if={i}, rftx={r}", fontsize=9)
        a.grid(alpha=0.3)

    for a in ax.flat[len(cfgs):]:
        a.axis("off")
    for n, a in enumerate(ax.flat[:len(cfgs)]):  # eixo x visível no último painel de cada coluna
        if n + nc >= len(cfgs):
            a.tick_params(labelbottom=True)
            a.set_xlabel("rf_gain do RX (dB)", fontsize=8)
    for a in ax[:, 0]:
        a.set_ylabel("BLER" if metrica == "bler" else "Vazão (kbps)", fontsize=8)

    fig.legend(handles=[Line2D([], [], color=f"C{k}", marker=MARCAS[k % len(MARCAS)],
                               label=rotulo(n)) for k, n in enumerate(dfs)],
               loc="lower center", ncol=min(len(dfs), 4), frameon=False, fontsize=9)
    if metrica == "bler":
        tit = f"BLER com IC 95% Wilson (BLER 0 plotado em {PISO:g}; tracejado = limiar){extra}"
    else:
        tit = f"Vazão com IC 95% Wilson do BLER convertido em vazão{extra}"
    fig.suptitle(tit, fontsize=11)
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    return fig


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csvs", nargs="+", help="um ou mais CSVs de varredura")
    p.add_argument("--saida", default="graficos", help="pasta de saída (padrão: graficos)")
    p.add_argument("--limiar", type=float, default=0.01,
                   help="BLER abaixo da qual o enlace é 'operacional' (padrão: 0.01)")
    p.add_argument("--faixa", metavar="MIN,MAX",
                   help="usa esta faixa de rf_gain (dB) no gráfico de zoom, em vez da "
                        "detectada automaticamente")
    p.add_argument("--vazao", action="store_true",
                   help="gera também os gráficos de vazão (completo e zoom)")
    p.add_argument("--rotulo", action="append", default=[], metavar="CHAVE=TEXTO",
                   help="nome na legenda para o CSV cujo nome termina em CHAVE (repetível)")
    p.add_argument("--mostrar", action="store_true", help="abre os gráficos na tela")
    a = p.parse_args()

    for r in a.rotulo:
        if "=" not in r:
            p.error(f"--rotulo precisa do formato CHAVE=TEXTO (recebido: {r!r})")
        chave, texto = r.split("=", 1)
        ROTULOS[chave] = texto

    faixa = None
    if a.faixa:
        try:
            faixa = tuple(float(x) for x in a.faixa.split(","))
            assert len(faixa) == 2 and faixa[0] < faixa[1]
        except (ValueError, AssertionError):
            p.error(f"--faixa precisa do formato MIN,MAX com MIN < MAX (recebido: {a.faixa!r})")

    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 160)
    dfs = {Path(f).stem: pd.read_csv(f) for f in a.csvs}

    for nome, df in dfs.items():
        print(f"\n=== {nome}: {len(df)} pontos, freq = {df.freq.iloc[0] / 1e6:g} MHz, "
              f"baud médio = {df.baud_rate.mean():.0f} ===")
        op = df[df.bler < a.limiar]
        print(f"operacionais (BLER < {a.limiar:g}): {len(op)}/{len(df)} "
              f"({100 * len(op) / len(df):.0f}%) | vazão máx = {df.vazao_bps.max():.0f} bps")
        if len(op):
            m = op.loc[op.vazao_bps.idxmax()]
            print(f"melhor ponto: iftx={m.iftxgain:g} rftx={m.rftxgain:g} "
                  f"rf_gain={m.rf_gain:g} -> BLER={m.bler:.2e}, vazão={m.vazao_bps:.0f} bps")
        r = resumo(df, a.limiar)
        print(r.to_string(index=False, float_format=lambda v: f"{v:.3g}"))
        r.to_csv(saida / f"{nome}_resumo.csv", index=False)

    prefixo = next(iter(dfs)) if len(dfs) == 1 else "comparacao"
    metricas = ["bler"] + (["vazao"] if a.vazao else [])

    zoom = faixa or intervalo_transicao(dfs)
    if zoom:
        m = 0.05 * (zoom[1] - zoom[0])
        xlim = (zoom[0] - m, zoom[1] + m)
        origem = "informada" if faixa else "detectada"
        print(f"\nIntervalo de zoom ({origem}): {zoom[0]:g} a {zoom[1]:g} dB")
    else:
        xlim = None
        print("\n(zoom pulado: nenhuma curva tem transição de BLER)")

    for met in metricas:
        fig = grafico_ic(dfs, a.limiar, met)
        fig.savefig(saida / f"{prefixo}_{'ic' if met == 'bler' else met}.png", dpi=150)
        figs = [fig]
        if xlim:
            fz = grafico_ic(dfs, a.limiar, met, xlim,
                            extra=f"\nzoom: rf_gain de {zoom[0]:g} a {zoom[1]:g} dB")
            fz.savefig(saida / f"{prefixo}_{'ic' if met == 'bler' else met}_zoom.png", dpi=150)
            figs.append(fz)
        if not a.mostrar:
            for f in figs:
                plt.close(f)

    print(f"\nGráficos e resumos salvos em: {saida}/")
    if a.mostrar:
        plt.show()


if __name__ == "__main__":
    main()
