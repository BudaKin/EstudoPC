#!/usr/bin/env python3
"""
Varredura do link real (HackRF -> RTL-SDR) para o qpsk_5g_hw.py. Grava BLER, vazão e baud rate em CSV.

Cada variável aceita:  INICIO FIM PASSO (varre)  ou  VALOR (fixa).
Modo 'tx+rx': HackRF e RTL-SDR neste computador. Modo 'rx': só o receptor (varia só rf_gain e freq).

Limites (HackRF One TX, RTL-SDR R820T RX):
  --iftxgain 0 a 47 dB (passo 1) | --rftxgain 0 ou 14 dB | --rf-gain os 29 ganhos do RTL-SDR
  --freq 24 a 1766 MHz

Exemplos:
  python3 varredura_hw.py --iftxgain 0 30 5
  python3 varredura_hw.py --iftxgain 0 30 10 --rf-gain 10 40 10 -o saida.csv
  python3 varredura_hw.py --modo rx --rf-gain 0 40 10

ATENÇÃO: a varredura mexe na potência transmitida. Com TX e RX por cabo ou muito
próximos, use atenuadores/afastamento para não saturar o RTL-SDR.
"""
import argparse, csv, sys, time
from itertools import product
from pathlib import Path

PADRAO = {'iftxgain': [0, 30, 5], 'rftxgain': [14], 'rf_gain': [29.7], 'freq': [670e6]}

# Limites do hardware. Qualquer valor pedido é ajustado ao válido mais próximo
# (acima do máximo vai para o máximo, abaixo do mínimo vai para o mínimo).
LIMITES = {
    'iftxgain': list(range(48)),   # HackRF One, ganho IF (VGA) de TX: 0 a 47 dB, passo de 1 dB
    'rftxgain': [0, 14],           # HackRF One, amplificador RF de TX: desligado (0) ou ligado (14 dB)
    'rf_gain': [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4,
                28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6],
}                                  # RTL-SDR (R820T), os 29 ganhos informados pelo rtl_test
FREQ_MIN, FREQ_MAX = 24e6, 1766e6  # faixa em comum: R820T (24-1766 MHz) e HackRF (1 MHz-6 GHz)

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
for nome, padrao in PADRAO.items():
    ap.add_argument('--' + nome.replace('_', '-'), dest=nome, nargs='+', type=float, default=padrao,
                    help=f'padrão: {" ".join(map(str, padrao))}')
ap.add_argument('--modo', choices=['tx+rx', 'rx'], default='tx+rx', help='padrão: tx+rx')
ap.add_argument('--tx-args', default='hackrf=0', help='padrão: hackrf=0')
ap.add_argument('--rx-args', default='rtl=0', help='padrão: rtl=0')
ap.add_argument('--amostras', type=float, default=50e6, help='amostras por ponto (padrão: 50e6)')
ap.add_argument('--descarte', type=float, default=2.4e6, help='amostras iniciais ignoradas (padrão: 2.4e6)')
ap.add_argument('--aquecimento', type=float, default=3.0, help='segundos de espera ao ligar (padrão: 3)')
ap.add_argument('-o', '--csv', default='resultados_hw.csv', help='arquivo de saída (padrão: resultados_hw.csv)')
args = ap.parse_args()


def valores(v):
    """[x] -> [x];  [inicio, fim, passo] -> lista de inicio até fim."""
    if len(v) not in (1, 3):
        ap.error('use VALOR ou INICIO FIM PASSO')
    if len(v) == 1 or v[2] == 0 or v[0] == v[1]:
        return [v[0]]
    ini, fim, passo = v
    passo = abs(passo) if fim > ini else -abs(passo)
    return [round(ini + i * passo, 12) for i in range(int((fim - ini) / passo + 1e-9) + 1)]


def ajustar(nome, vals):
    """Ajusta cada valor ao mais próximo que o hardware aceita (fora do limite vai para a borda)."""
    if nome == 'freq':
        ajustados = [min(max(v, FREQ_MIN), FREQ_MAX) for v in vals]
    else:
        ajustados = [min(LIMITES[nome], key=lambda x: abs(x - v)) for v in vals]
    if ajustados != vals:
        print(f'aviso: {nome} ajustado ao que o hardware aceita: {vals} -> {ajustados}')
    return list(dict.fromkeys(ajustados))   # tira repetidos criados pelo ajuste


def esperar(medidor, alvo):
    """Espera o contador de amostras chegar a 'alvo' (erro se travar por 10 s)."""
    ultimo, t = medidor.n_tx, time.monotonic()
    while medidor.n_tx < alvo:
        if medidor.n_tx != ultimo:
            ultimo, t = medidor.n_tx, time.monotonic()
        elif time.monotonic() - t > 10:
            raise RuntimeError('o flowgraph parou de produzir amostras')
        time.sleep(0.01)


nomes = list(PADRAO)
listas = {n: ajustar(n, valores(getattr(args, n))) for n in nomes}
amostras, descarte = int(args.amostras), int(args.descarte)
if amostras <= descarte:
    ap.error('--amostras deve ser maior que --descarte')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpsk_5g_hw as hw   # flowgraph original, sem alterações

fatores = hw.fatores_do_modo(args.modo)
fora = [n for n in nomes if n not in fatores and getattr(args, n) != PADRAO[n]]
if fora:
    sys.exit(f'no modo {args.modo!r} só dá para variar: {", ".join(fatores)}')
nomes = [n for n in nomes if n in fatores]
pontos = [dict(zip(nomes, c)) for c in product(*(listas[n] for n in nomes))]

tb = hw.qpsk_5g_hw(modo=args.modo, tx_args=args.tx_args, rx_args=args.rx_args)
m = tb.epy_block_1   # medidor: n_tx = amostras, n_ok = bits que passaram no CRC
print(f'{len(pontos)} pontos x {amostras:,} amostras '
      f'(~{(len(pontos) * amostras / tb.get_rx_samp_rate() + args.aquecimento) / 60:.1f} min)')


def aplicar(ponto, anterior):
    """Muda só o que mudou, para não re-sintonizar o rádio à toa."""
    for nome, valor in ponto.items():
        if anterior.get(nome) != valor:
            getattr(tb, 'set_' + nome)(valor)


aplicar(pontos[0], {})   # primeiro ponto já vale na partida
tb.start()
tb.flowgraph_started.set()
try:
    time.sleep(args.aquecimento)
    with open(args.csv, 'w', newline='') as f:
        saida = csv.DictWriter(f, [*nomes, 'amostras', 'quadros', 'erros', 'bler', 'vazao_bps', 'baud_rate'])
        saida.writeheader()
        anterior = pontos[0]
        for i, ponto in enumerate(pontos, 1):
            aplicar(ponto, anterior)
            anterior = ponto

            base = m.n_tx
            esperar(m, base + descarte)
            tx0, ok0, t0 = m.n_tx, m.n_ok, time.monotonic()
            esperar(m, base + amostras)
            tx1, ok1, t1 = m.n_tx, m.n_ok, time.monotonic()

            quadros = (tx1 - tx0) // m.amostras_por_quadro
            erros = max(quadros - (ok1 - ok0) // m.bits_por_quadro, 0)
            bler = erros / quadros if quadros else ''
            vazao = (ok1 - ok0) / (t1 - t0)                       # bits corretos / s
            baud = (tx1 - tx0) / (t1 - t0) / tb.get_rx_sps()      # símbolos / s

            saida.writerow({**ponto, 'amostras': tx1 - tx0, 'quadros': quadros, 'erros': erros,
                            'bler': bler, 'vazao_bps': round(vazao, 1), 'baud_rate': round(baud, 1)})
            f.flush()   # não perde nada se interromper
            print(f'[{i}/{len(pontos)}] {ponto}  ->  BLER {bler or 0:.2e}  {vazao / 1e3:.1f} kbit/s')
except KeyboardInterrupt:
    print('\nInterrompido.')
finally:
    tb.stop()
    tb.wait()
print(f'Resultados em {args.csv}')
