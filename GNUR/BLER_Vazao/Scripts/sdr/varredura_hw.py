#!/usr/bin/env python3
"""Varredura do link HackRF -> RTL-SDR. Grava BLER e vazão em CSV.
Cada variável aceita INICIO FIM PASSO (varre) ou VALOR (fixa). Começa do maior ganho. Exemplo:
  python3 varredura_hw.py --iftx 0 45 5 --rftx 14 --rfrx 0 50 10 -o saida.csv"""
import argparse, sys, time
from pathlib import Path
import pandas as pd

# A ordem aqui é a do aninhamento: a primeira variável muda mais devagar, a última mais rápido.
# O iftx fica por último: é o que muda a cada ponto, sem precisar re-sintonizar o rádio.
PADRAO = {'rftxgain': [14], 'rf_gain': [29.7], 'freq': [670e6], 'iftxgain': [0, 30, 5]}
# Valores que o hardware aceita. O que você pedir é trocado pelo mais próximo.
LIMITES = {'iftxgain': range(48),   # HackRF: ganho IF de TX (dB)
           'rftxgain': [0, 14],     # HackRF: amplificador de TX desligado/ligado
           'rf_gain': [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4,
                       28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]}
DESCARTE = 2.4e6   # amostras do começo de cada ponto que não entram na medição
AQUECIMENTO = 3    # segundos de espera depois de iniciar o flowgraph

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
OPCOES = {'iftxgain': '--iftx', 'rftxgain': '--rftx', 'rf_gain': '--rfrx', 'freq': '--freq'}
for nome, padrao in PADRAO.items():
    ap.add_argument(OPCOES[nome], dest=nome, nargs='+', type=float, default=padrao)
ap.add_argument('--amostras', type=float, default=50e6, help='amostras por ponto (padrão: 50e6)')
ap.add_argument('-o', '--csv', default='resultados_hw.csv')
args = ap.parse_args()
if args.amostras <= DESCARTE:
    sys.exit(f'--amostras deve ser maior que {DESCARTE:g}')


def faixa(v):
    """VALOR -> [VALOR];  INICIO FIM PASSO -> INICIO, INICIO+PASSO, ... até FIM."""
    if len(v) == 1:
        return v
    ini, fim, passo = v
    passo = abs(passo) if fim >= ini else -abs(passo)
    return [round(ini + i * passo, 6) for i in range(int((fim - ini) / passo + 1e-9) + 1)]


def ajustar(nome, v):
    if nome == 'freq':
        return min(max(v, 24e6), 1766e6)
    return min(LIMITES[nome], key=lambda x: abs(x - v))


def esperar(alvo):
    while m.n_tx < alvo:
        time.sleep(0.01)


def aplicar(ponto):
    """Muda só o que mudou, para não re-sintonizar o rádio à toa."""
    for nome, valor in ponto.items():
        if atual.get(nome) != valor:
            getattr(tb, 'set_' + nome)(valor)
    atual.update(ponto)


def formatar(seg):
    h, resto = divmod(int(seg), 3600)
    mn, s = divmod(resto, 60)
    return f'{h}h{mn:02d}min{s:02d}s'


listas = []
for nome in PADRAO:
    ajustados = {ajustar(nome, v) for v in faixa(getattr(args, nome))}   # o conjunto tira repetidos
    listas.append(sorted(ajustados, reverse=nome != 'freq'))             # ganhos: do maior para o menor

# Em serpente: o laço de dentro inverte o sentido a cada passo do de fora, então cada ponto
# difere do anterior em uma só variável e em um só degrau (sem saltos grandes).
pontos = [()]
for lista in reversed(listas):
    pontos = [(v, *p) for i, v in enumerate(lista) for p in (pontos if i % 2 == 0 else pontos[::-1])]
pontos = [dict(zip(PADRAO, p)) for p in pontos]

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpsk_5g_hw as hw   # flowgraph original
tb = hw.qpsk_5g_hw(modo='tx+rx', tx_args='hackrf=0', rx_args='rtl=0')
m = tb.epy_block_1        # medidor: n_tx = amostras recebidas, n_ok = bits que passaram no CRC
atual, linhas, amostras = {}, [], int(args.amostras)

# Estimativa: cada ponto dura (amostras / taxa de amostragem) segundos.
taxa_amostragem = tb.get_baud_rate() * tb.get_rx_sps()
estimado = AQUECIMENTO + len(pontos) * amostras / taxa_amostragem
print(f'{len(pontos)} pontos, tempo estimado: {formatar(estimado)}')

aplicar(pontos[0])        # o primeiro ponto já vale na partida
tb.start()
tb.flowgraph_started.set()
try:
    time.sleep(AQUECIMENTO)
    for i, ponto in enumerate(pontos, 1):
        aplicar(ponto)
        base = m.n_tx
        esperar(base + DESCARTE)                          # deixa o receptor estabilizar
        tx0, ok0, t0 = m.n_tx, m.n_ok, time.monotonic()
        esperar(base + amostras)                          # janela de medição
        tx1, ok1, t1 = m.n_tx, m.n_ok, time.monotonic()

        quadros = (tx1 - tx0) // m.amostras_por_quadro
        erros = max(quadros - (ok1 - ok0) // m.bits_por_quadro, 0)
        bler = erros / quadros
        vazao = (ok1 - ok0) / (t1 - t0)                   # bits corretos por segundo
        baud = (tx1 - tx0) / (t1 - t0) / tb.get_rx_sps()  # símbolos por segundo
        linhas.append({**ponto, 'amostras': tx1 - tx0, 'quadros': quadros, 'erros': erros,
                       'bler': bler, 'vazao_bps': round(vazao, 1), 'baud_rate': round(baud, 1)})
        print(f'[{i}/{len(pontos)}] {ponto}  ->  BLER {bler:.2e}  {vazao / 1e3:.1f} kbit/s')
except KeyboardInterrupt:
    print('\nInterrompido.')
finally:
    tb.stop()
    tb.wait()
    pd.DataFrame(linhas).to_csv(args.csv, index=False)   # grava o que foi medido, mesmo se interromper
    print(f'Resultados em {args.csv}')