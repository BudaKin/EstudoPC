#!/usr/bin/env python3
"""Analisa CSVs de varredura de hardware (iftxgain, rftxgain, rf_gain, bler, vazao_bps...).

Gráficos por arquivo:  mapas, curvas, categorico, ic, ganho_total
Gráficos entre arquivos (usam todos os CSVs juntos):  fronteira, comparacao

Exemplos:
  python analise.py resultados_hw_casa1210.csv
  python analise.py resultados_hw_*.csv --saida graficos --limiar 0.01
  python analise.py resultados_hw_*.csv --graficos fronteira,ic --mostrar
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap, LogNorm
from matplotlib.patches import Patch

PISO = 1e-5  # BLER = 0 não aparece em escala log; plota como este piso
MARCAS = "o^sD"
ESTILOS = ["-", "--", ":", "-."]


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


# ---------- gráficos por arquivo: f(df, titulo, limiar) -> fig ----------

def mapas(df, titulo, limiar):
    """Linha 1: BLER (log). Linha 2: vazão. Uma coluna por rftxgain."""
    rfs = sorted(df.rftxgain.unique())
    fig, ax = plt.subplots(2, len(rfs), figsize=(6.5 * len(rfs), 8), squeeze=False)
    vmax = df.vazao_bps.max()
    for j, r in enumerate(rfs):
        d = df[df.rftxgain == r]
        for linha, col in enumerate(["bler", "vazao_bps"]):
            t = d.pivot(index="iftxgain", columns="rf_gain", values=col).sort_index()
            a = ax[linha, j]
            if col == "bler":
                im = a.imshow(t.clip(lower=PISO), aspect="auto", origin="lower",
                              cmap="viridis_r", norm=LogNorm(PISO, 1))
            else:
                im = a.imshow(t / 1e3, aspect="auto", origin="lower",
                              cmap="viridis", vmin=0, vmax=vmax / 1e3)
            a.set_xticks(range(len(t.columns)), [f"{c:g}" for c in t.columns])
            a.set_yticks(range(len(t.index)), t.index)
            a.set_xlabel("rf_gain do RX (dB)")
            a.set_ylabel("iftxgain")
            a.set_title(f"{'BLER' if col == 'bler' else 'Vazão (kbps)'} | rftxgain = {r}")
            fig.colorbar(im, ax=a)
            if col == "bler":  # marca células abaixo do limiar
                for (y, x), v in np.ndenumerate(t.values):
                    if v < limiar:
                        a.plot(x, y, "w.", ms=4)
    fig.suptitle(f"{titulo}  (pontos brancos: BLER < {limiar:g})")
    fig.tight_layout()
    return fig


def curvas(df, titulo, limiar):
    """BLER x rf_gain, uma curva por iftxgain, um painel por rftxgain."""
    rfs = sorted(df.rftxgain.unique())
    fig, ax = plt.subplots(1, len(rfs), figsize=(6.5 * len(rfs), 4.5), squeeze=False,
                           sharey=True)
    for a, r in zip(ax[0], rfs):
        for i, g in df[df.rftxgain == r].groupby("iftxgain"):
            g = g.sort_values("rf_gain")
            a.semilogy(g.rf_gain, g.bler.clip(lower=PISO), "o-", ms=4, label=f"if={i}")
        a.axhline(limiar, color="k", ls="--", lw=0.8)
        a.set_xlabel("rf_gain do RX (dB)")
        a.set_title(f"rftxgain = {r}")
        a.grid(alpha=0.3)
    ax[0, 0].set_ylabel(f"BLER (0 plotado em {PISO:g}; tracejado = limiar)")
    ax[0, -1].legend(fontsize=8, ncol=2)
    fig.suptitle(titulo)
    fig.tight_layout()
    return fig


def categorico(df, titulo, limiar):
    """Mapa em 3 níveis: falha (>=10%), degradado (limiar a 10%), operacional (<limiar)."""
    rfs = sorted(df.rftxgain.unique())
    cores = ["#2b2b2b", "#e6ab02", "#1b9e77"]
    nomes = ["falha (BLER ≥ 10%)", f"degradado ({limiar:g} ≤ BLER < 10%)",
             f"operacional (BLER < {limiar:g})"]
    fig, ax = plt.subplots(1, len(rfs), figsize=(6 * len(rfs), 4.6), squeeze=False)
    for a, r in zip(ax[0], rfs):
        t = df[df.rftxgain == r].pivot(index="iftxgain", columns="rf_gain",
                                       values="bler").sort_index()
        nivel = np.select([t.values < limiar, t.values < 0.1], [2, 1], 0)
        a.imshow(nivel, aspect="auto", origin="lower", cmap=ListedColormap(cores),
                 vmin=-0.5, vmax=2.5)
        a.set_xticks(range(len(t.columns)), [f"{c:g}" for c in t.columns])
        a.set_yticks(range(len(t.index)), t.index)
        a.set_xlabel("rf_gain do RX (dB)")
        a.set_ylabel("iftxgain")
        a.set_title(f"rftxgain = {r}")
    fig.legend(handles=[Patch(color=c, label=n) for c, n in zip(cores, nomes)],
               loc="lower center", ncol=3, frameon=False)
    fig.suptitle(titulo)
    fig.tight_layout(rect=[0, 0.08, 1, 1])
    return fig


def ic(df, titulo, limiar):
    """Um painel por configuração de TX: BLER com intervalo de confiança de Wilson (95%)."""
    cfgs = sorted(df.groupby(["rftxgain", "iftxgain"]).groups)
    nc = min(5, len(cfgs))
    nl = -(-len(cfgs) // nc)
    fig, ax = plt.subplots(nl, nc, figsize=(3.2 * nc, 2.6 * nl), squeeze=False,
                           sharex=True, sharey=True)
    for a, (r, i) in zip(ax.flat, cfgs):
        g = df[(df.rftxgain == r) & (df.iftxgain == i)].sort_values("rf_gain")
        lo, hi = wilson(g.erros.values, g.quadros.values)
        a.fill_between(g.rf_gain, np.clip(lo, PISO, 1), np.clip(hi, PISO, 1), alpha=0.3)
        a.semilogy(g.rf_gain, g.bler.clip(lower=PISO), "o-", ms=3)
        a.axhline(limiar, color="k", ls="--", lw=0.7)
        a.set_title(f"if={i}, rftx={r}", fontsize=9)
        a.grid(alpha=0.3)
    for a in ax.flat[len(cfgs):]:
        a.axis("off")
    for a in ax[-1]:
        a.set_xlabel("rf_gain do RX (dB)", fontsize=8)
    fig.suptitle(f"{titulo}  (faixa = IC 95% Wilson; BLER 0 plotado em {PISO:g})")
    fig.tight_layout()
    return fig


def ganho_total(df, titulo, limiar):
    """BLER x (iftxgain + rftxgain + rf_gain): as curvas colapsam se só a potência importa."""
    fig, a = plt.subplots(figsize=(8, 4.8))
    vmin, vmax = df.iftxgain.min(), df.iftxgain.max()
    for r, m in zip(sorted(df.rftxgain.unique()), MARCAS):
        d = df[df.rftxgain == r]
        sc = a.scatter(d.iftxgain + d.rftxgain + d.rf_gain, d.bler.clip(lower=PISO),
                       c=d.iftxgain, cmap="viridis", vmin=vmin, vmax=vmax, marker=m,
                       s=28, edgecolors="k", linewidths=0.3, label=f"rftxgain = {r}")
    a.set_yscale("log")
    a.axhline(limiar, color="k", ls="--", lw=0.8)
    a.set_xlabel("iftxgain + rftxgain + rf_gain (dB)")
    a.set_ylabel(f"BLER (0 plotado em {PISO:g})")
    a.grid(alpha=0.3)
    a.legend(loc="lower left", fontsize=8)
    fig.colorbar(sc, label="iftxgain")
    a.set_title(titulo)
    fig.tight_layout()
    return fig


# ---------- gráficos entre arquivos: f(dfs, limiar) -> fig ----------

def fronteira(dfs, limiar):
    """Menor rf_gain a partir do qual o enlace opera, em função do iftxgain."""
    topo = max(d.rf_gain.max() for d in dfs.values()) + 4
    fig, a = plt.subplots(figsize=(8, 4.8))
    for k, (nome, df) in enumerate(dfs.items()):
        cor = f"C{k}"
        for (r, d), est, m in zip(df.groupby("rftxgain"), ESTILOS, MARCAS):
            xs, ys = [], []
            for i, g in d.groupby("iftxgain"):
                g = g.sort_values("rf_gain")
                ruim = np.where(g.bler.values >= limiar)[0]
                ini = ruim[-1] + 1 if len(ruim) else 0
                xs.append(i)
                ys.append(g.rf_gain.iloc[ini] if ini < len(g) else np.nan)
            ys = np.array(ys, float)
            a.plot(xs, ys, ls=est, marker=m, color=cor, label=f"{nome} | rftx={r}")
            nao = np.isnan(ys)
            a.plot(np.array(xs)[nao], np.full(nao.sum(), topo), "x", color=cor, ms=8)
    a.set_ylim(-2, topo + 3)
    a.axhline(topo, color="gray", lw=0.6, ls=":")
    a.text(a.get_xlim()[0], topo + 0.6, " × = não opera em nenhum rf_gain", fontsize=8,
           color="gray")
    a.set_xlabel("iftxgain")
    a.set_ylabel("menor rf_gain do RX com BLER < limiar para todos os maiores (dB)")
    a.set_title(f"Fronteira de operação (BLER < {limiar:g})")
    a.grid(alpha=0.3)
    a.legend(fontsize=8)
    fig.tight_layout()
    return fig


def comparacao(dfs, limiar):
    """BLER x rf_gain sobrepondo os arquivos, nas configurações de TX em comum."""
    if len(dfs) < 2:
        return None
    ifs = sorted(set.intersection(*[set(d.iftxgain) for d in dfs.values()]))
    rfs = sorted(set.intersection(*[set(d.rftxgain) for d in dfs.values()]))
    if not ifs or not rfs:
        return None
    fig, ax = plt.subplots(len(rfs), len(ifs), figsize=(3.2 * len(ifs), 2.8 * len(rfs)),
                           squeeze=False, sharex=True, sharey=True)
    for y, r in enumerate(rfs):
        for x, i in enumerate(ifs):
            a = ax[y, x]
            for nome, df in dfs.items():
                g = df[(df.iftxgain == i) & (df.rftxgain == r)].sort_values("rf_gain")
                a.semilogy(g.rf_gain, g.bler.clip(lower=PISO), "o-", ms=3, label=nome)
            a.axhline(limiar, color="k", ls="--", lw=0.7)
            a.set_title(f"if={i}, rftx={r}", fontsize=9)
            a.grid(alpha=0.3)
    for a in ax[-1]:
        a.set_xlabel("rf_gain do RX (dB)", fontsize=8)
    ax[0, 0].legend(fontsize=7)
    fig.suptitle(f"Comparação entre arquivos (BLER 0 plotado em {PISO:g})")
    fig.tight_layout()
    return fig


POR_ARQUIVO = {"mapas": mapas, "curvas": curvas, "categorico": categorico, "ic": ic,
               "ganho_total": ganho_total}
ENTRE_ARQUIVOS = {"fronteira": fronteira, "comparacao": comparacao}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csvs", nargs="+", help="um ou mais CSVs de varredura")
    p.add_argument("--saida", default="graficos", help="pasta de saída (padrão: graficos)")
    p.add_argument("--limiar", type=float, default=0.01,
                   help="BLER abaixo da qual o enlace é 'operacional' (padrão: 0.01)")
    p.add_argument("--graficos", default="todos",
                   help="lista separada por vírgula ou 'todos' (padrão). Opções: "
                        + ", ".join([*POR_ARQUIVO, *ENTRE_ARQUIVOS]))
    p.add_argument("--mostrar", action="store_true", help="abre os gráficos na tela")
    a = p.parse_args()

    validos = [*POR_ARQUIVO, *ENTRE_ARQUIVOS]
    sel = validos if a.graficos == "todos" else a.graficos.split(",")
    for s in sel:
        if s not in validos:
            p.error(f"gráfico desconhecido: {s!r} (opções: {', '.join(validos)})")

    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 160)
    dfs = {Path(f).stem: pd.read_csv(f) for f in a.csvs}

    def salvar(fig, nome):
        if fig is None:
            print(f"(pulado: {nome} precisa de ≥2 arquivos com configurações em comum)")
            return
        fig.savefig(saida / f"{nome}.png", dpi=150)
        if not a.mostrar:
            plt.close(fig)

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
        for g in sel:
            if g in POR_ARQUIVO:
                salvar(POR_ARQUIVO[g](df, nome, a.limiar), f"{nome}_{g}")

    for g in sel:
        if g in ENTRE_ARQUIVOS:
            salvar(ENTRE_ARQUIVOS[g](dfs, a.limiar), g)

    print(f"\nGráficos e resumos salvos em: {saida}/")
    if a.mostrar:
        plt.show()


if __name__ == "__main__":
    main()
