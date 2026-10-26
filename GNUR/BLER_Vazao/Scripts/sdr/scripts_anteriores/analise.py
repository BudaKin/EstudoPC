#!/usr/bin/env python3
"""Analisa CSVs da varredura de hardware (varredura_hw.py): BLER e vazão com IC de Wilson (95%).

Eixo x: detectado sozinho. Entre iftxgain, rf_gain, freq e rftxgain, é a variável com mais valores
distintos dentro de um CSV (o R só vira eixo se nada mais variar). --x força.

Quadros: dependem de quantas variáveis variam além do eixo x (ordenadas por nº de valores, da que
tem mais para a que tem menos; --paineis escolhe outra ordem):
  nenhuma     -> 1 quadro
  1 (A)       -> 1 quadro por valor de A (em linhas de até 5)
  2 (A, B)    -> grade X x Y: X colunas = valores de A, Y linhas = valores de B
  3 ou mais   -> a grade acima, e as variáveis que sobram viram curvas de cores diferentes
Com vários CSVs, cada um é uma curva (uma cor) em todos os quadros, com o nome na legenda.
O que não varia vai no título. Funciona com CSVs antigos (sem N, K e R) e novos.

Saídas (só imagens, em --saida), um arquivo para o BLER e outro para a vazão:
  <prefixo>_ic.png          BLER x eixo x, faixa completa
  <prefixo>_ic_zoom.png     BLER, só o intervalo em que ele realmente varia (detectado sozinho)
  <prefixo>_vazao.png       vazão x eixo x, faixa completa
  <prefixo>_vazao_zoom.png  vazão, no mesmo intervalo do zoom
O prefixo é o nome do CSV (se for um só) ou 'comparacao' (se forem vários).
O resumo de cada CSV (pontos operacionais, melhor ponto...) só é impresso no terminal.

Exemplos:
  python analise.py resultado_2210.csv
  python analise.py resultado_2210.csv resultado_22101.csv --rotulo 2210=manhã --rotulo 22101=tarde
  python analise.py resultado_2210.csv --x iftxgain --paineis R,rf_gain --faixa 10,30
"""
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

PISO = 1e-5  # BLER = 0 não aparece em escala log; plota como este piso
MARCAS = "o^sDvP*X"
VARIAVEIS = ["R", "iftxgain", "rftxgain", "rf_gain", "freq"]   # o que a varredura pode variar
PREFERENCIA_X = ["rf_gain", "iftxgain", "freq", "rftxgain"]    # desempate para o eixo x
ORDEM = PREFERENCIA_X + ["R"]                                 # desempate entre variáveis de quadros
OBRIGATORIAS = ["bler", "erros", "quadros", "vazao_bps"]
NOMES_X = {
    "iftxgain": "iftx: ganho IF do TX (dB)",
    "rftxgain": "rftx: amplificador do TX (dB)",
    "rf_gain": "rf_gain do RX (dB)",
    "freq": "frequência (MHz)",
    "R": "taxa do código polar R = K/N",
}
DESCRICAO = {
    "R": lambda v: f"R={v:.3f}",
    "iftxgain": lambda v: f"iftx={v:g} dB",
    "rftxgain": lambda v: f"rftx={v:g} dB",
    "rf_gain": lambda v: f"rf_gain={v:g} dB",
    "freq": lambda v: f"{v / 1e6:g} MHz",
    "N": lambda v: f"N={v:g}",
}
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


# ---------------------------------------------------------------- leitura e detecção do eixo

def nominal_por_linha(df):
    """Vazão sem erros (bps) da taxa R de cada linha: vazão = nominal * (1 - BLER), então o máximo de
    vazao/(1-BLER) entre os pontos do mesmo R (com BLER < 1) é o nominal. NaN se o R nunca decodificou."""
    taxa = (df.vazao_bps / (1 - df.bler)).where(df.bler < 1)
    return taxa.groupby(df["R"], dropna=False).transform("max")


def carregar(caminhos):
    """Lê os CSVs. Colunas que o CSV não tem (R, nos antigos) ficam como NaN."""
    dfs = {}
    for f in caminhos:
        df = pd.read_csv(f)
        falta = [c for c in OBRIGATORIAS if c not in df.columns]
        if falta:
            raise SystemExit(f"{f}: faltam as colunas {', '.join(falta)} (não parece um CSV da varredura)")
        for v in VARIAVEIS:
            if v not in df.columns:
                df[v] = np.nan
        df["R"] = df["R"].round(4)          # tira ruído de float na hora de agrupar
        df["_nominal"] = nominal_por_linha(df)
        nome, n = Path(f).stem, 2
        while nome in dfs:                  # dois CSVs com o mesmo nome em pastas diferentes
            nome, n = f"{Path(f).stem}_{n}", n + 1
        dfs[nome] = df
    return dfs


def variaveis_que_variam(dfs):
    """{variável: maior nº de valores distintos que ela tem dentro de um mesmo CSV}, só as que variam."""
    n = {v: max(df[v].nunique() for df in dfs.values()) for v in VARIAVEIS}
    return {v: k for v, k in n.items() if k > 1}


def escolher_x(varia):
    """A variável com mais valores distintos; empate pela ordem de PREFERENCIA_X. R só se não houver outra."""
    candidatas = [v for v in PREFERENCIA_X if v in varia]
    if candidatas:
        return max(candidatas, key=lambda v: (varia[v], -PREFERENCIA_X.index(v)))
    return "R" if "R" in varia else None


def fixas(dfs, usadas):
    """O que não varia: devolve (descrições comuns a todos os CSVs, {csv: descrições só dele}).
    As comuns vão no título; as que diferem entre CSVs vão junto do nome na legenda."""
    comuns, por_csv = [], {nome: [] for nome in dfs}
    for v in VARIAVEIS + ["N"]:
        if v in usadas or not any(v in df.columns for df in dfs.values()):
            continue
        valor = {nome: (df[v].dropna().iloc[0] if v in df.columns and df[v].notna().any() else None)
                 for nome, df in dfs.items()}
        presentes = {x for x in valor.values() if x is not None}
        if not presentes:
            continue
        if len(presentes) == 1 and all(x is not None for x in valor.values()):
            comuns.append(DESCRICAO[v](presentes.pop()))
        elif v != "N":
            for nome, x in valor.items():
                if x is not None:
                    por_csv[nome].append(DESCRICAO[v](x))
    return comuns, por_csv


def agrupar(df, series):
    """[(valores das séries, linhas)] de um CSV; sem séries, uma única curva."""
    if not series:
        return [((), df)]
    return [(k if isinstance(k, tuple) else (k,), g) for k, g in df.groupby(series, dropna=False)]


def descrever(v, valor):
    return f"{v}=?" if pd.isna(valor) else DESCRICAO[v](valor)


def valores_de(dfs, v):
    """Valores distintos de v em todos os CSVs, em ordem; NaN (CSV sem a coluna) por último."""
    vals = sorted({x for df in dfs.values() for x in df[v].dropna().unique()})
    return vals + [np.nan] if any(df[v].isna().any() for df in dfs.values()) else vals


def selecionar(df, variaveis, valores):
    """Linhas de df em que cada variável vale o valor dado (NaN casa com NaN)."""
    m = pd.Series(True, index=df.index)
    for v, val in zip(variaveis, valores):
        m &= df[v].isna() if pd.isna(val) else (df[v] == val)
    return df[m]


def montar_layout(dfs, x, pvars, series, extras_csv):
    """Distribui as curvas nos quadros: 0 variáveis de quadro -> 1; 1 -> um por valor; 2 -> grade colunas x linhas.
    Cada curva é um (CSV, valores das séries) dentro do quadro; a cor vem do texto da curva."""
    def texto(nome, chave):
        partes = extras_csv[nome] + [descrever(v, k) for v, k in zip(series, chave) if pd.notna(k)]
        cabeca = rotulo(nome) if len(dfs) > 1 else ""
        return " | ".join(t for t in (cabeca, ", ".join(partes)) if t) or rotulo(nome)

    textos = []   # ordem fixa das curvas (e das cores), igual em todos os quadros
    for nome, df in dfs.items():
        for chave, _ in agrupar(df, series):
            if texto(nome, chave) not in textos:
                textos.append(texto(nome, chave))

    vals = [valores_de(dfs, v) for v in pvars]
    if not pvars:
        nl = nc = 1
        grade = [((), 0, 0)]
    elif len(pvars) == 1:
        nc = min(5, len(vals[0]))
        nl = -(-len(vals[0]) // nc)
        grade = [((v,), i // nc, i % nc) for i, v in enumerate(vals[0])]
    else:
        nc, nl = len(vals[0]), len(vals[1])      # X colunas (1ª variável) x Y linhas (2ª)
        grade = [((a, b), j, i) for j, b in enumerate(vals[1]) for i, a in enumerate(vals[0])]

    celulas = []
    for chave, r, c in grade:
        curvas = []
        for nome, df in dfs.items():
            for ch, g in agrupar(selecionar(df, pvars, chave), series):
                g = g.sort_values(x)
                curvas.append({"texto": texto(nome, ch), "g": g, "xs": g[x] / 1e6 if x == "freq" else g[x]})
        titulo = ", ".join(descrever(v, k) for v, k in zip(pvars, chave))
        celulas.append({"r": r, "c": c, "titulo": titulo, "curvas": curvas})
    return {"nl": nl, "nc": nc, "celulas": celulas, "textos": textos}


def intervalo_transicao(curvas):
    """Faixa do eixo x em que o BLER sai do patamar de falha (~1) e chega ao de zero erros.

    Por curva, considera 'em transição' os pontos cujo IC de Wilson está acima do piso e
    abaixo de 99%, mais um ponto vizinho de cada lado; devolve a união entre todas as
    curvas. None se nenhuma curva transita.
    """
    lo, hi = np.inf, -np.inf
    for c in curvas:
        l, h = wilson(c["g"].erros.values, c["g"].quadros.values)
        idx = np.where((l > PISO) & (h < 0.99))[0]
        if not len(idx):
            continue
        xs = c["xs"].values
        lo = min(lo, xs[max(idx[0] - 1, 0)])
        hi = max(hi, xs[min(idx[-1] + 1, len(xs) - 1)])
    return (lo, hi) if lo < hi else None


# ---------------------------------------------------------------- gráfico

def cor(k, n):
    mapa, tam = ("tab10", 10) if n <= 10 else ("tab20", 20)
    return plt.get_cmap(mapa)(k % tam)


def grafico(layout, x, limiar, metrica="bler", xlim=None, fixos="", extra=""):
    """Um quadro por célula do layout; uma cor por curva; faixa = IC 95% de Wilson."""
    nl, nc, celulas, textos = layout["nl"], layout["nc"], layout["celulas"], layout["textos"]
    n = len(textos)
    maior = max(len(t) for t in textos)
    ncol = max(1, min(n, 4, 95 // (maior + 8)))   # colunas da legenda: as que cabem na largura
    linhas_leg = -(-n // ncol)
    if nl * nc == 1:
        tam = (8.5, 5.0 + 0.25 * linhas_leg)
    else:
        tam = (min(max(3.2 * nc, 7.5), 28), max(2.6 * nl, 5) + 0.5 + 0.25 * linhas_leg)
    fig, ax = plt.subplots(nl, nc, figsize=tam, squeeze=False, sharex=True, sharey=True)

    usadas = set()
    for cel in celulas:
        if not cel["curvas"]:
            continue
        a = ax[cel["r"], cel["c"]]
        usadas.add((cel["r"], cel["c"]))
        for cv in cel["curvas"]:
            g, xs = cv["g"], cv["xs"]
            k = textos.index(cv["texto"])
            lo, hi = wilson(g.erros.values, g.quadros.values)
            col, marca = cor(k, n), MARCAS[k % len(MARCAS)]
            if metrica == "bler":
                a.fill_between(xs, np.clip(lo, PISO, 1), np.clip(hi, PISO, 1), color=col, alpha=0.2)
                a.plot(xs, g.bler.clip(lower=PISO), marker=marca, ms=4, color=col)
            else:
                nominal = g["_nominal"].values / 1e3   # próprio de cada R (muda com a taxa)
                a.fill_between(xs, nominal * (1 - hi), nominal * (1 - lo), color=col, alpha=0.2)
                a.plot(xs, g.vazao_bps / 1e3, marker=marca, ms=4, color=col)
        if metrica == "bler":
            a.set_yscale("log")
            a.set_ylim(PISO / 2, 2)
            a.axhline(limiar, color="k", ls="--", lw=0.7)
        if metrica != "bler":
            a.set_ylim(bottom=0)
        a.set_title(cel["titulo"], fontsize=9)
        a.grid(alpha=0.3)

    valores = sorted({v for cel in celulas for cv in cel["curvas"] for v in cv["xs"]})
    if xlim:
        valores = [v for v in valores if xlim[0] <= v <= xlim[1]]
    for r in range(nl):
        for c in range(nc):
            a = ax[r, c]
            if (r, c) not in usadas:
                a.axis("off")
                continue
            if xlim:
                a.set_xlim(*xlim)
            if len(valores) <= 14:
                a.set_xticks(valores)
            if not any(rr > r and cc == c for rr, cc in usadas):    # último quadro visível da coluna
                a.tick_params(labelbottom=True)
                a.set_xlabel(NOMES_X[x], fontsize=8)
            if not any(cc < c and rr == r for rr, cc in usadas):    # primeiro quadro visível da linha
                a.set_ylabel("BLER" if metrica == "bler" else "Vazão (kbps)", fontsize=8)

    if metrica == "bler":
        tit = f"BLER com IC 95% Wilson (BLER 0 plotado em {PISO:g}; tracejado = limiar)"
    else:
        tit = "Vazão com IC 95% Wilson do BLER convertido em vazão"
    tit += extra + (f"\nfixos: {fixos}" if fixos else "")
    fig.suptitle(tit, fontsize=10)
    fig.legend(handles=[Line2D([], [], color=cor(k, n), marker=MARCAS[k % len(MARCAS)], label=t)
                        for k, t in enumerate(textos)],
               loc="lower center", ncol=ncol, frameon=False, fontsize=9)
    fig.tight_layout(rect=[0, 0.05 * linhas_leg + 0.02, 1, 1])
    return fig


# ---------------------------------------------------------------- resumo (só no terminal)

def resumo(df, x, series, limiar):
    """Uma linha por curva do CSV (valores das séries)."""
    linhas = []
    for chave, g in agrupar(df, series):
        ok = g[g.bler < limiar]
        linha = {v: k for v, k in zip(series, chave)}
        linha.update({
            "pontos": len(g), "operacionais": len(ok),
            f"{x}_min_ok": ok[x].min() if len(ok) else np.nan,
            f"{x}_max_ok": ok[x].max() if len(ok) else np.nan,
            "bler_min": g.bler.min(),
            "vazao_max_bps": g.vazao_bps.max(),
        })
        linhas.append(linha)
    return pd.DataFrame(linhas)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csvs", nargs="+", help="um ou mais CSVs de varredura")
    p.add_argument("--saida", default="graficos", help="pasta de saída dos gráficos (padrão: graficos)")
    p.add_argument("--x", choices=VARIAVEIS, metavar="VARIAVEL",
                   help=f"força o eixo x ({', '.join(VARIAVEIS)}); por padrão é detectado")
    p.add_argument("--paineis", metavar="VAR1[,VAR2]",
                   help="variáveis dos quadros: VAR1 nas colunas e VAR2 nas linhas (por padrão, as que "
                        "variam depois do eixo x, da de mais valores para a de menos)")
    p.add_argument("--limiar", type=float, default=0.01,
                   help="BLER abaixo da qual o enlace é 'operacional' (padrão: 0.01)")
    p.add_argument("--faixa", metavar="MIN,MAX",
                   help="usa esta faixa do eixo x (na unidade dele, ex.: dB) no gráfico de zoom, "
                        "em vez da detectada automaticamente")
    p.add_argument("--vazao", action="store_true", help=argparse.SUPPRESS)   # antigo: agora sempre gera
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
            faixa = tuple(float(v) for v in a.faixa.split(","))
            assert len(faixa) == 2 and faixa[0] < faixa[1]
        except (ValueError, AssertionError):
            p.error(f"--faixa precisa do formato MIN,MAX com MIN < MAX (recebido: {a.faixa!r})")

    dfs = carregar(a.csvs)
    varia = variaveis_que_variam(dfs)
    x = a.x or escolher_x(varia)
    if x is None:
        p.error("nenhuma variável varia nos CSVs: não há o que colocar no eixo x")
    if x not in varia:
        p.error(f"--x {x}: essa variável tem um valor só em cada CSV")
    # Das variáveis que variam além do x (da de mais valores para a de menos): as duas primeiras
    # definem os quadros, as que sobram viram curvas.
    resto = sorted((v for v in varia if v != x), key=lambda v: (-varia[v], ORDEM.index(v)))
    if a.paineis:
        escolhidas = [v.strip() for v in a.paineis.split(",")]
        if len(escolhidas) > 2 or len(set(escolhidas)) != len(escolhidas) or any(v not in resto for v in escolhidas):
            p.error(f"--paineis aceita até 2 variáveis diferentes, entre as que variam além do eixo x "
                    f"({', '.join(resto) or 'nenhuma'}); recebido: {a.paineis!r}")
        resto = escolhidas + [v for v in resto if v not in escolhidas]
    pvars, series = resto[:2], resto[2:]
    comuns, extras_csv = fixas(dfs, [x, *resto])
    fixos = ", ".join(comuns)
    layout = montar_layout(dfs, x, pvars, series, extras_csv)

    print(f"Eixo x: {x} ({varia[x]} valores)" + (" [forçado por --x]" if a.x else " [detectado]"))
    if not pvars:
        print("Quadros: 1 (só o eixo x varia)")
    elif len(pvars) == 1:
        print(f"Quadros: {len(layout['celulas'])}, um por valor de {pvars[0]}")
    else:
        print(f"Quadros: {layout['nc']} x {layout['nl']} (colunas = {pvars[0]}, linhas = {pvars[1]})")
    if series or len(dfs) > 1:
        print("Curvas por: " + ", ".join([f"{v} ({varia[v]} valores)" for v in series] + (["CSV"] if len(dfs) > 1 else [])))
    if fixos:
        print(f"Fixos: {fixos}")

    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 160)

    for nome, df in dfs.items():
        print(f"\n=== {nome}: {len(df)} pontos, freq = {df.freq.iloc[0] / 1e6:g} MHz, "
              f"baud médio = {df.baud_rate.mean():.0f} ===")
        op = df[df.bler < a.limiar]
        print(f"operacionais (BLER < {a.limiar:g}): {len(op)}/{len(df)} "
              f"({100 * len(op) / len(df):.0f}%) | vazão máx = {df.vazao_bps.max():.0f} bps")
        if len(op):
            m = op.loc[op.vazao_bps.idxmax()]
            pt = ", ".join(DESCRICAO[v](m[v]) for v in VARIAVEIS if pd.notna(m[v]))
            print(f"melhor ponto: {pt} -> BLER={m.bler:.2e}, vazão={m.vazao_bps:.0f} bps")
        print(resumo(df, x, resto, a.limiar).to_string(index=False, float_format=lambda v: f"{v:.3g}"))

    curvas = [cv for cel in layout["celulas"] for cv in cel["curvas"]]   # todas, para achar o zoom
    prefixo = next(iter(dfs)) if len(dfs) == 1 else "comparacao"
    metricas = ["bler", "vazao"]

    zoom = faixa or intervalo_transicao(curvas)
    if zoom:
        m = 0.05 * (zoom[1] - zoom[0])
        xlim = (zoom[0] - m, zoom[1] + m)
        origem = "informada" if faixa else "detectada"
        print(f"\nIntervalo de zoom ({origem}): {zoom[0]:g} a {zoom[1]:g}")
    else:
        xlim = None
        print("\n(zoom pulado: nenhuma curva tem transição de BLER)")

    gerados = []
    for met in metricas:
        base = f"{prefixo}_{'ic' if met == 'bler' else met}"
        fig = grafico(layout, x, a.limiar, met, fixos=fixos)
        fig.savefig(saida / f"{base}.png", dpi=150)
        gerados.append(f"{base}.png")
        figs = [fig]
        if xlim:
            fz = grafico(layout, x, a.limiar, met, xlim, fixos=fixos,
                         extra=f"\nzoom: {x} de {zoom[0]:g} a {zoom[1]:g}")
            fz.savefig(saida / f"{base}_zoom.png", dpi=150)
            gerados.append(f"{base}_zoom.png")
            figs.append(fz)
        if not a.mostrar:
            for f in figs:
                plt.close(f)

    print(f"\nGráficos salvos em {saida}/:")
    for nome_arq in gerados:
        print(f"  {nome_arq}")
    if a.mostrar:
        plt.show()


if __name__ == "__main__":
    main()
