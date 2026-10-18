#!/usr/bin/env python3
"""Varredura do link HackRF -> RTL-SDR. Grava BLER e vazão em CSV.
Cada variável aceita INICIO FIM PASSO (varre) ou VALOR (fixa). Começa do maior ganho. Exemplo:
  python3 varredura_hw-c.py --iftx 0 45 5 --rftx 14 --rfrx 0 50 10 -o saida.csv

Como a BLER é medida: o TX numera os quadros (bloco 'Numerador de quadros' do qpsk_5g_hw.py).
Em cada ponto, depois do descarte, o script anota o número do próximo quadro que o TX vai gerar
(A) e mede exatamente os quadros A, A+1, ..., A+N-1. Cada um deles ou sai certo do CRC no RX ou
conta como erro. Nada é estimado pelo tempo: perdas no começo, no meio ou no fim da janela entram
na conta do mesmo jeito. Precisa do qpsk_5g_hw.py com a numeração de quadros (mesma pasta).

Colunas do CSV: os ganhos do ponto; amostras (duração da janela, em amostras do RTL-SDR);
quadros (N); erros; bler; vazao_bps (bits certos por segundo); baud_rate (taxa de amostras
entregue pelo RTL-SDR / sps: só confere o RTL, não o enlace); rajadas (quantas sequências de
quadros perdidos); maior_rajada (a mais longa, em quadros)."""
import argparse, csv, sys, threading, time
from pathlib import Path

# A ordem aqui é a do aninhamento: a primeira variável muda mais devagar, a última mais rápido.
# O iftx fica por último: é o que muda a cada ponto, sem precisar re-sintonizar o rádio.
PADRAO = {'rftxgain': [14], 'rf_gain': [29.7], 'freq': [670e6], 'iftxgain': [0, 30, 5]}
# Valores que o hardware aceita. O que você pedir é trocado pelo mais próximo.
LIMITES = {'iftxgain': range(48),   # HackRF: ganho IF de TX (dB)
           'rftxgain': [0, 14],     # HackRF: amplificador de TX desligado/ligado
           'rf_gain': [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4,
                       28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]}
DESCARTE = 2.4e6   # amostras do RTL-SDR (1 s) entre mudar o ganho e começar a medir
AQUECIMENTO = 3    # segundos de espera depois de iniciar o flowgraph
LATENCIA_PADRAO = 3000   # quadros (~4 s): atraso TX->RX máximo, se não der para medir no aquecimento
PARADO = 5         # s sem amostras do RTL-SDR (ou sem quadros novos no TX) = algo travou

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
OPCOES = {'iftxgain': '--iftx', 'rftxgain': '--rftx', 'rf_gain': '--rfrx', 'freq': '--freq'}
for nome, padrao in PADRAO.items():
    ap.add_argument(OPCOES[nome], dest=nome, nargs='+', type=float, default=padrao)
ap.add_argument('--amostras', type=float, default=50e6,
                help='amostras do RTL-SDR por ponto, contando o descarte (padrão: 50e6, ~21 s, 15025 quadros)')
ap.add_argument('--erros', type=int, default=0,
                help='encerra o ponto assim que chegar a este número de erros (padrão: 0 = nunca)')
ap.add_argument('-o', '--csv', default='resultados_hw.csv')
args = ap.parse_args()
if args.amostras <= DESCARTE:
    sys.exit(f'--amostras deve ser maior que {DESCARTE:g}')
if args.erros < 0:
    sys.exit('--erros não pode ser negativo')


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
    """Espera o RTL-SDR entregar 'alvo' amostras. Para com erro se ele deixar de entregar."""
    visto, t = c.n, time.monotonic()
    while c.n < alvo:
        time.sleep(0.01)
        if c.n != visto:
            visto, t = c.n, time.monotonic()
        elif time.monotonic() - t > PARADO:
            raise RuntimeError('o RTL-SDR parou de entregar amostras')


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
import qpsk_5g_hw as hw   # flowgraph (versão com numeração de quadros)
from gnuradio import gr
import numpy as np


class ContadorAmostras(gr.sync_block):
    """Sorvedouro que só conta as amostras que chegam do RTL-SDR: marca o descarte de cada ponto."""
    def __init__(self):
        gr.sync_block.__init__(self, name='Contador de amostras', in_sig=[np.complex64], out_sig=None)
        self.n = 0

    def work(self, input_items, output_items):
        self.n += len(input_items[0])
        return len(input_items[0])


class ContadorQuadros(gr.sync_block):
    """Fica na saída do CRC (só passam quadros certos) e anota o número de cada quadro que chega."""
    def __init__(self, bits_por_quadro, bits_numero):
        gr.sync_block.__init__(self, name='Contador de quadros', in_sig=[np.uint8], out_sig=None)
        self.bits_por_quadro, self.bits_numero = bits_por_quadro, bits_numero
        self._pesos = 1 << np.arange(bits_numero - 1, -1, -1, dtype=np.int64)
        self._sobra = np.zeros(0, dtype=np.uint8)   # pedaço de quadro que chegou incompleto
        self._chegadas = []
        self._trava = threading.Lock()              # retirar() é chamado da thread principal

    def work(self, input_items, output_items):
        L = self.bits_por_quadro
        bits = np.concatenate((self._sobra, input_items[0]))
        n = bits.size // L
        self._sobra = bits[n * L:].copy()
        if n:
            numeros = bits[:n * L].reshape(n, L)[:, :self.bits_numero].astype(np.int64) @ self._pesos
            with self._trava:
                self._chegadas.extend(numeros.tolist())
        return len(input_items[0])

    def retirar(self):
        """Números dos quadros certos que chegaram desde a última chamada, na ordem de chegada."""
        with self._trava:
            chegadas, self._chegadas = self._chegadas, []
        return chegadas


tb = hw.qpsk_5g_hw(modo='tx+rx', tx_args='hackrf=0', rx_args='rtl=0')
if not hasattr(tb, 'epy_block_2'):
    sys.exit('este script precisa do qpsk_5g_hw.py com numeração de quadros (Numerador de quadros no TX)')
numerador = tb.epy_block_2       # TX: escreve o número em cada quadro
costas = tb.digital_costas_loop_cc_0
c = ContadorAmostras()
q = ContadorQuadros(numerador.bits_por_quadro, numerador.bits_numero)
tb.connect((tb.osmosdr_source_0, 0), (c, 0))
tb.connect((tb.digital_crc32_bb_0, 0), (q, 0))
MODULO = 1 << numerador.bits_numero
atual = {}

taxa_amostragem = tb.get_baud_rate() * tb.get_rx_sps()
amostras_por_quadro = (tb.get_sync_len() + tb.get_N()) // 2 * tb.get_rx_sps()   # QPSK: 2 bits/símbolo
periodo_quadro = amostras_por_quadro / taxa_amostragem                         # s entre quadros
n_quadros = int((args.amostras - DESCARTE) // amostras_por_quadro)              # quadros medidos por ponto
if n_quadros < 1:
    sys.exit('--amostras pequeno demais: não cabe nem um quadro depois do descarte')


def quadro_tx():
    """Quantos quadros o TX já gerou (o próximo a ser gerado recebe este número)."""
    return numerador.nitems_written(0) // numerador.bits_por_quadro


def medir_latencia(segundos=2.0):
    """Maior atraso, em quadros, entre o TX gerar um quadro e ele sair do CRC no RX (None se nada chegou)."""
    maior = None
    q.retirar()
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        time.sleep(0.05)
        chegou = q.retirar()
        if chegou:
            atraso = (quadro_tx() - chegou[-1]) % MODULO
            maior = atraso if maior is None else max(maior, atraso)
    return maior


def medir(n, limite_erros):
    """Mede os quadros A, A+1, ..., A+n-1, com A = próximo quadro que o TX vai gerar.
    Devolve (quadros decididos, quadros certos, rajadas de perdas, maior rajada)."""
    q.retirar()                          # o que chegou até aqui é de antes da janela
    A = quadro_tx()
    ok, ultimo, rajadas, maior = 0, -1, 0, 0   # ultimo: posição (0..n-1) do último quadro certo
    visto, t_visto = A, time.monotonic()
    while True:
        time.sleep(0.02)
        passou = False
        for x in q.retirar():
            d = (x - A) % MODULO         # posição do quadro na janela
            if d >= MODULO // 2 or d <= ultimo:
                continue                 # de antes da janela (ou repetido)
            if d >= n:
                passou = True            # já é de depois da janela
                continue
            falta = d - ultimo - 1       # quadros que não chegaram entre dois certos
            if falta:
                rajadas += 1
                maior = max(maior, falta)
            ok += 1
            ultimo = d
        gerado = quadro_tx()
        if gerado != visto:
            visto, t_visto = gerado, time.monotonic()
        elif time.monotonic() - t_visto > PARADO:
            raise RuntimeError('o TX (HackRF) parou de consumir amostras')
        # Até onde já dá para decidir: os quadros chegam em ordem, então tudo antes do último
        # que chegou já está decidido; e o que o TX gerou há mais de 'latencia' quadros já
        # teria chegado se fosse chegar.
        decidido = n if passou else min(n, max(ultimo + 1, gerado - latencia - A))
        if decidido >= n or (limite_erros and decidido - ok >= limite_erros):
            break
    falta = decidido - ultimo - 1        # perdidos no fim da janela
    if falta > 0:
        rajadas += 1
        maior = max(maior, falta)
    return decidido, ok, rajadas, maior


estimado = AQUECIMENTO + len(pontos) * (args.amostras / taxa_amostragem + 1.5)
print(f'{len(pontos)} pontos, {n_quadros} quadros por ponto, tempo estimado: {formatar(estimado)}'
      + (' (menos, se --erros encerrar pontos antes)' if args.erros else ''))

colunas = list(PADRAO) + ['amostras', 'quadros', 'erros', 'bler', 'vazao_bps', 'baud_rate',
                          'rajadas', 'maior_rajada']
arquivo = open(args.csv, 'w', newline='')
escritor = csv.DictWriter(arquivo, fieldnames=colunas)
escritor.writeheader()
arquivo.flush()

aplicar(pontos[0])        # o primeiro ponto já vale na partida
tb.start()
tb.flowgraph_started.set()
try:
    time.sleep(AQUECIMENTO)
    medida = medir_latencia()
    latencia = 2 * medida + 200 if medida is not None else LATENCIA_PADRAO
    f_bom = costas.get_frequency() if medida is not None else 0.0
    print(f'atraso TX->RX medido: {medida} quadros; limite usado: {latencia} quadros '
          f'({latencia * periodo_quadro:.1f} s)')
    for i, ponto in enumerate(pontos, 1):
        aplicar(ponto)
        # Se o sinal sumiu no ponto anterior, o Costas pode ter ficado travado numa frequência
        # errada (e não sai de lá nem com sinal bom). Volta para a do último ponto com enlace.
        costas.set_frequency(f_bom)
        esperar(c.n + DESCARTE)                           # deixa o receptor estabilizar
        a0, t0 = c.n, time.monotonic()
        quadros, ok, rajadas, maior = medir(n_quadros, args.erros)
        a1, t1 = c.n, time.monotonic()
        erros = quadros - ok
        bler = erros / quadros
        if bler < 0.1:
            f_bom = costas.get_frequency()
        vazao = ok * numerador.bits_por_quadro / (quadros * periodo_quadro)   # bits certos por segundo
        baud = (a1 - a0) / (t1 - t0) / tb.get_rx_sps()
        escritor.writerow({**ponto, 'amostras': quadros * amostras_por_quadro, 'quadros': quadros,
                           'erros': erros, 'bler': bler, 'vazao_bps': round(vazao, 1),
                           'baud_rate': round(baud, 1), 'rajadas': rajadas, 'maior_rajada': maior})
        arquivo.flush()                                   # cada ponto vai para o disco na hora
        texto = f'BLER {bler:.2e}' if erros else f'BLER < {3 / quadros:.1e} (0 erros; 95% de confiança)'
        print(f'[{i}/{len(pontos)}] {ponto}  ->  {texto}  {vazao / 1e3:.1f} kbit/s, '
              f'quadros: {quadros}, erros: {erros}, rajadas: {rajadas}')
except KeyboardInterrupt:
    print('\nInterrompido.')
finally:
    tb.stop()
    tb.wait()
    arquivo.close()
    print(f'Resultados em {args.csv}')
