#!/usr/bin/env python3
"""
Varredura do canal para o script.py (QPSK 5G). Grava BLER, vazão e baud rate em CSV.

Cada variável aceita:  INICIO FIM PASSO (varre)  ou  VALOR (fixa).

Exemplos:
  python3 varredura.py --noise-voltage 1 3 0.4
  python3 varredura.py --noise-voltage 2 --freq-offset 0 500 100 -o saida.csv
"""
import argparse, csv, sys, time
from itertools import product
from pathlib import Path

PADRAO = {'noise_voltage': [1, 3, 0.4], 'epsilon': [1], 'freq_offset': [0]}

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
for nome, padrao in PADRAO.items():
    ap.add_argument('--' + nome.replace('_', '-'), dest=nome, nargs='+', type=float, default=padrao,
                    help=f'padrão: {" ".join(map(str, padrao))}')
ap.add_argument('--amostras', type=float, default=50e6, help='amostras por ponto (padrão: 50e6)')
ap.add_argument('--descarte', type=float, default=200e3, help='amostras iniciais ignoradas (padrão: 200e3)')
ap.add_argument('-o', '--csv', default='resultados.csv', help='arquivo de saída (padrão: resultados.csv)')
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
pontos = [dict(zip(nomes, c)) for c in product(*(valores(getattr(args, n)) for n in nomes))]
amostras, descarte = int(args.amostras), int(args.descarte)
if amostras <= descarte:
    ap.error('--amostras deve ser maior que --descarte')

sys.path.insert(0, str(Path(__file__).resolve().parent))
import script   # flowgraph original, sem alterações

tb = script.qpsk_5g_claude()
m = tb.epy_block_1   # medidor: n_tx = amostras, n_ok = bits que passaram no CRC
print(f'{len(pontos)} pontos x {amostras:,} amostras (~{len(pontos) * amostras / tb.get_samp_rate() / 60:.1f} min)')

tb.start()
tb.flowgraph_started.set()
try:
    with open(args.csv, 'w', newline='') as f:
        saida = csv.DictWriter(f, [*nomes, 'amostras', 'quadros', 'erros', 'bler', 'vazao_bps', 'baud_rate'])
        saida.writeheader()
        for i, ponto in enumerate(pontos, 1):
            for nome, valor in ponto.items():
                getattr(tb, 'set_' + nome)(valor)

            base = m.n_tx
            esperar(m, base + descarte)
            tx0, ok0, t0 = m.n_tx, m.n_ok, time.monotonic()
            esperar(m, base + amostras)
            tx1, ok1, t1 = m.n_tx, m.n_ok, time.monotonic()

            quadros = (tx1 - tx0) // m.amostras_por_quadro
            erros = max(quadros - (ok1 - ok0) // m.bits_por_quadro, 0)
            bler = erros / quadros if quadros else ''
            vazao = (ok1 - ok0) / (t1 - t0)                    # bits corretos / s
            baud = (tx1 - tx0) / (t1 - t0) / tb.get_sps()      # símbolos / s

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
