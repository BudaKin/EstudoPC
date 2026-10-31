#!/usr/bin/env python3
"""Varredura do link HackRF -> RTL-SDR. Grava BLER e vazão em CSV.
Cada variável aceita INICIO FIM PASSO (varre) ou VALOR (fixa). Começa do maior ganho. Exemplo:
  python3 varredura.py --iftx 0 45 5 --rftx 14 --rfrx 0 50 10 -o saida.csv
O --R (taxa do código polar K/N, aceita 0.5 ou 1/2) segue a mesma regra: VALOR fixa, INICIO FIM PASSO varre
(padrão: --R 1/3 2/3 1/6, ou seja, 1/3, 1/2 e 2/3; --R 0.1 0.9 0.1 varre de 0,1 a 0,9). A varredura de
ganhos roda inteira para o primeiro R, depois para o segundo, e assim por diante. O R só se define na
criação do flowgraph, então a cada troca ele é derrubado (soltando HackRF e RTL-SDR) e criado de novo.
--N e --amostras valem para a varredura inteira.

Como a BLER é medida: o TX numera os quadros (bloco 'Numerador de quadros' do qpsk_5g_hw.py).
Em cada ponto, depois do descarte, o script anota o número do próximo quadro que o TX vai gerar
(A) e mede exatamente os quadros A, A+1, ..., A+N-1. Cada um deles ou sai certo do CRC no RX ou
conta como erro. Nada é estimado pelo tempo: perdas no começo, no meio ou no fim da janela entram
na conta do mesmo jeito. Precisa do qpsk_5g_hw.py com a numeração de quadros (mesma pasta).

Antes de varrer cada taxa, o script espera o enlace subir: só começa quando o primeiro quadro certo sair
do CRC (no máximo --espera segundos, padrão 60). Se estourar, avisa e varre mesmo assim.

Num terminal, o progresso fica num painel fixo no rodapé (ponto atual, fase, quadros e erros da
medição em andamento, tempo decorrido e restante); as mensagens rolam acima dele. --sem-painel desliga.

Arquivo de saída: sem -o, o nome é resultado_DDMM.csv (DD = dia, MM = mês) no 1º teste do dia e
resultado_DDMM1.csv, resultado_DDMM2.csv... nos seguintes (ex.: 2210, 22101, 22102). Com -o, usa o nome dado.

Colunas do CSV: N, K e R do código polar; os ganhos do ponto; amostras (duração da janela, em amostras do RTL-SDR);
quadros (N); erros; bler; vazao_bps (bits certos por segundo); baud_rate (taxa de amostras
entregue pelo RTL-SDR / sps: só confere o RTL, não o enlace); rajadas (quantas sequências de
quadros perdidos); maior_rajada (a mais longa, em quadros)."""
import argparse, csv, datetime, gc, re, shutil, sys, threading, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qpsk_5g_hw as hw   # flowgraph (versão com numeração de quadros e --R/--N)

if not hasattr(hw, 'epy_block_2'):
    sys.exit('este script precisa do qpsk_5g_hw.py com numeração de quadros (Numerador de quadros no TX)')
# O qpsk_5g_hw inicia threads de painel (probes) que nunca terminam e seguram o flowgraph na memória.
# Aqui não servem (a varredura tem os próprios contadores) e impediriam soltar os rádios entre um R e outro.
hw.qpsk_5g_hw._iniciar_probe = lambda self, leitura, setter, periodo=0.5: None

# A ordem aqui é a do aninhamento: a primeira variável muda mais devagar, a última mais rápido.
# O iftx fica por último: é o que muda a cada ponto, sem precisar re-sintonizar o rádio.
PADRAO = {'rftxgain': [14], 'rf_gain': [29.7], 'freq': [670e6], 'iftxgain': [0, 30, 5]}
# Valores que o hardware aceita. O que você pedir é trocado pelo mais próximo.
LIMITES = {'iftxgain': range(48),   # HackRF: ganho IF de TX (dB)
           'rftxgain': [0, 14],     # HackRF: amplificador de TX desligado/ligado
           'rf_gain': [0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7, 22.9, 25.4,
                       28.0, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4, 43.9, 44.5, 48.0, 49.6]}
DESCARTE = 2.4e6   # amostras do RTL-SDR (1 s) entre mudar o ganho e começar a medir
QUADROS_INICIAIS = 1     # quadros certos que precisam chegar para considerar o enlace de pé
LATENCIA_PADRAO = 3000   # quadros (~4 s): atraso TX->RX máximo, se não der para medir no aquecimento
ALTURA_PAINEL = 6   # linhas reservadas no rodapé do terminal (separador + 5 linhas)
PAUSA_ENTRE_R = 2   # s depois de derrubar o flowgraph, para os rádios serem soltos
TENTATIVAS_RADIO = 4   # vezes que tenta abrir os rádios na troca de R (se ainda estiverem ocupados)
PARADO = 5         # s sem amostras do RTL-SDR (ou sem quadros novos no TX) = algo travou

def nome_resultado(pasta=Path('.')):
    """resultado_DDMM.csv no 1º teste do dia; depois resultado_DDMM1.csv, resultado_DDMM2.csv, ...
    O número é o maior já usado no dia + 1 (o arquivo sem número conta como 0)."""
    base = f'resultado_{datetime.date.today():%d%m}'
    usados = []
    for arq in pasta.glob(base + '*.csv'):
        m = re.fullmatch(re.escape(base) + r'(\d*)\.csv', arq.name)
        if m:
            usados.append(int(m.group(1) or 0))
    return f'{base}.csv' if not usados else f'{base}{max(usados) + 1}.csv'


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
OPCOES = {'iftxgain': '--iftx', 'rftxgain': '--rftx', 'rf_gain': '--rfrx', 'freq': '--freq'}
for nome, padrao in PADRAO.items():
    ap.add_argument(OPCOES[nome], dest=nome, nargs='+', type=float, default=padrao)
ap.add_argument('--amostras', type=float, default=50e6,
                help='amostras do RTL-SDR por ponto, contando o descarte (padrão: 50e6, ~21 s, 15025 quadros)')
ap.add_argument('--erros', type=int, default=0,
                help='encerra o ponto assim que chegar a este número de erros (padrão: 0 = nunca)')
ap.add_argument('--espera', type=float, default=60, metavar='S',
                help='segundos, no máximo, esperando o primeiro quadro certo antes de varrer cada taxa (padrão: 60; '
                     '0 = não espera). Se estourar, avisa e varre mesmo assim')
ap.add_argument('--sem-painel', action='store_true',
                help='não reserva o rodapé do terminal; só imprime mensagens (útil para redirecionar a saída)')
ap.add_argument('--R', type=hw.fracao, nargs='+', default=[1 / 3, 2 / 3, 1 / 6], metavar='TAXA',
                help='taxa do código polar K/N, ex.: 1/3, 0.5: VALOR fixa; INICIO FIM PASSO varre '
                     '(padrão: 1/3 2/3 1/6 = 1/3, 1/2, 2/3); cada R roda a varredura de ganhos inteira')
ap.add_argument('--N', type=int, default=1024, metavar='BITS',
                help='bits codificados por bloco, potência de 2 até 1024 (padrão: 1024); vale para toda a varredura')
ap.add_argument('-o', '--csv', default=None, metavar='ARQUIVO',
                help='arquivo de saída (padrão: resultado_DDMM.csv; nos testes seguintes do mesmo dia, '
                     'resultado_DDMM1.csv, resultado_DDMM2.csv, ...)')
args = ap.parse_args()
if args.csv is None:
    args.csv = nome_resultado()
if args.amostras <= DESCARTE:
    sys.exit(f'--amostras deve ser maior que {DESCARTE:g}')
if args.erros < 0:
    sys.exit('--erros não pode ser negativo')
if args.espera < 0:
    sys.exit('--espera não pode ser negativo')
def faixa(v, opcao='valor'):
    """VALOR -> [VALOR];  INICIO FIM PASSO -> INICIO, INICIO+PASSO, ... até FIM."""
    if len(v) == 1:
        return v
    if len(v) != 3:
        sys.exit(f'{opcao}: use VALOR ou INICIO FIM PASSO (recebi {len(v)} valores)')
    ini, fim, passo = v
    if ini == fim:
        return [ini]
    if passo == 0:
        sys.exit(f'{opcao}: o PASSO não pode ser 0')
    passo = abs(passo) if fim >= ini else -abs(passo)
    if abs(passo) > abs(fim - ini):
        print(f'AVISO: {opcao}: o passo ({abs(passo):g}) é maior que o intervalo ({abs(fim - ini):g}); '
              f'só vale o valor inicial ({ini:g}). Era uma lista de valores? Use INICIO FIM PASSO.')
    return [round(ini + i * passo, 6) for i in range(int((fim - ini) / passo + 1e-9) + 1)]


Ks = []                        # K de cada taxa, na ordem pedida (sem repetir)
for r in faixa(args.R, '--R'):
    K = round(r * args.N)
    try:
        hw.validar_codigo(args.N, K)
    except ValueError as e:
        sys.exit(f'erro: R = {r:g}: {e}')
    if K not in Ks:
        Ks.append(K)


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
    ajustados = {ajustar(nome, v) for v in faixa(getattr(args, nome), OPCOES[nome])}   # o conjunto tira repetidos
    listas.append(sorted(ajustados, reverse=nome != 'freq'))             # ganhos: do maior para o menor

# Em serpente: o laço de dentro inverte o sentido a cada passo do de fora, então cada ponto
# difere do anterior em uma só variável e em um só degrau (sem saltos grandes).
pontos = [()]
for lista in reversed(listas):
    pontos = [(v, *p) for i, v in enumerate(lista) for p in (pontos if i % 2 == 0 else pontos[::-1])]
pontos = [dict(zip(PADRAO, p)) for p in pontos]

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


atual = {}
# O que o painel mostra. A thread principal escreve, a do painel só lê.
estado = {'fase': 'iniciando', 'i': 0, 'r_i': 0, 'K': None, 'concluidos': 0, 'ponto': {}, 'ultimo': None,
          'decidido': 0, 'ok': 0, 't_inicio': time.monotonic(), 't_ponto': None, 't_fase': None}
# Peças do R atual: montar() cria, liberar() derruba. As funções abaixo as lêem daqui.
tb = numerador = costas = c = q = None
MODULO = taxa_amostragem = amostras_por_quadro = periodo_quadro = n_quadros = None


def montar(K, tentativas=1):
    """Cria o flowgraph da taxa K/N (é aqui que HackRF e RTL-SDR são abertos) e as peças da medição.
    Na troca de R os rádios podem demorar a ser soltos, então tenta algumas vezes."""
    global tb, numerador, costas, c, q, MODULO
    global taxa_amostragem, amostras_por_quadro, periodo_quadro, n_quadros
    for tentativa in range(1, tentativas + 1):
        try:
            tb = hw.qpsk_5g_hw(modo='tx+rx', tx_args='hackrf=0', rx_args='rtl=0', N=args.N, K=K)
            break
        except RuntimeError as e:
            if tentativa == tentativas:
                raise RuntimeError(f'não consegui abrir os rádios: {e}. Se foi na troca de R, rode uma taxa '
                                   f'por vez (--R 1/3, depois --R 1/2, ...)') from e
            print(f'rádios ainda ocupados ({e}); tentando de novo...')
            gc.collect()
            time.sleep(PAUSA_ENTRE_R)
    numerador = tb.epy_block_2       # TX: escreve o número em cada quadro
    costas = tb.digital_costas_loop_cc_0
    c = ContadorAmostras()
    q = ContadorQuadros(numerador.bits_por_quadro, numerador.bits_numero)
    tb.connect((tb.osmosdr_source_0, 0), (c, 0))
    tb.connect((tb.digital_crc32_bb_0, 0), (q, 0))
    MODULO = 1 << numerador.bits_numero
    atual.clear()                    # o flowgraph novo começa com os ganhos padrão: reaplica tudo

    taxa_amostragem = tb.get_baud_rate() * tb.get_rx_sps()
    amostras_por_quadro = (tb.get_sync_len() + tb.get_N()) // 2 * tb.get_rx_sps()   # QPSK: 2 bits/símbolo
    periodo_quadro = amostras_por_quadro / taxa_amostragem                         # s entre quadros
    n_quadros = int((args.amostras - DESCARTE) // amostras_por_quadro)              # quadros medidos por ponto
    if n_quadros < 1:
        sys.exit('--amostras pequeno demais: não cabe nem um quadro depois do descarte')


def liberar(pausa=True):
    """Para e destrói o flowgraph atual, soltando HackRF e RTL-SDR para o próximo R."""
    global tb, numerador, costas, c, q
    estado['fase'] = 'liberando os rádios'    # o painel só lê c/taxa nas fases de medição
    if tb is not None:
        tb.stop()
        tb.wait()
    tb = numerador = costas = c = q = None    # sem referências, o GNU Radio fecha os rádios
    gc.collect()
    if pausa:
        time.sleep(PAUSA_ENTRE_R)


def quadro_tx():
    """Quantos quadros o TX já gerou (o próximo a ser gerado recebe este número)."""
    return numerador.nitems_written(0) // numerador.bits_por_quadro


def medir_latencia(segundos=2.0):
    """Maior atraso, em quadros, entre o TX gerar um quadro e ele sair do CRC no RX (None se nada chegou)."""
    maior = None
    estado['fase'], estado['t_fase'] = 'latencia', time.monotonic()
    q.retirar()
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        time.sleep(0.05)
        chegou = q.retirar()
        if chegou:
            atraso = (quadro_tx() - chegou[-1]) % MODULO
            maior = atraso if maior is None else max(maior, atraso)
    return maior


def aguardar_primeiros_quadros(limite, minimo=QUADROS_INICIAIS):
    """Espera o enlace subir: volta quando 'minimo' quadros certos tiverem saído do CRC.
    Devolve True se subiu, False se passaram 'limite' s sem isso. Dá erro se o RTL-SDR deixar
    de entregar amostras ou se o TX parar de gerar quadros (não adianta esperar nesses casos)."""
    if limite <= 0:
        return False
    q.retirar()                          # descarta o que chegou antes de começarmos a esperar
    inicio = ultimo_aviso = time.monotonic()
    chegaram = 0
    estado.update(fase='espera', t_fase=inicio, chegaram=0, minimo=minimo, limite=limite)
    visto_rx, t_rx = c.n, inicio
    visto_tx, t_tx = quadro_tx(), inicio
    while True:
        time.sleep(0.05)
        agora = time.monotonic()
        chegaram += len(q.retirar())
        estado['chegaram'] = chegaram
        if chegaram >= minimo:
            return True
        if agora - inicio >= limite:
            return False
        if c.n != visto_rx:
            visto_rx, t_rx = c.n, agora
        elif agora - t_rx > PARADO:
            raise RuntimeError('o RTL-SDR parou de entregar amostras')
        gerado = quadro_tx()
        if gerado != visto_tx:
            visto_tx, t_tx = gerado, agora
        elif agora - t_tx > PARADO:
            raise RuntimeError('o TX (HackRF) parou de consumir amostras')
        if agora - ultimo_aviso >= 5:
            ultimo_aviso = agora
            print(f'  ainda sem quadro certo ({agora - inicio:.0f} s de {limite:g} s)...')


def medir(n, limite_erros):
    """Mede os quadros A, A+1, ..., A+n-1, com A = próximo quadro que o TX vai gerar.
    Devolve (quadros decididos, quadros certos, rajadas de perdas, maior rajada)."""
    estado.update(fase='medindo', decidido=0, ok=0, t_fase=time.monotonic())
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
        estado['decidido'], estado['ok'] = decidido, ok
        if decidido >= n or (limite_erros and decidido - ok >= limite_erros):
            break
    falta = decidido - ultimo - 1        # perdidos no fim da janela
    if falta > 0:
        rajadas += 1
        maior = max(maior, falta)
    return decidido, ok, rajadas, maior


def barra(fracao, largura=20):
    n = round(min(max(fracao, 0.0), 1.0) * largura)
    return '[' + '#' * n + '.' * (largura - n) + ']'


def linhas_painel():
    """As 5 linhas de dados do painel, a partir de 'estado'."""
    agora = time.monotonic()
    p, fase = estado['ponto'], estado['fase']
    total, feitos = len(pontos) * len(Ks), estado['concluidos']   # pontos de todas as taxas
    if estado['K']:
        sequencia = (' -> '.join(f'{k / args.N:.3f}' for k in Ks) if len(Ks) <= 5 else
                     f'{Ks[0] / args.N:.3f} ... {Ks[-1] / args.N:.3f}')
        l0 = (f" Taxa {estado['r_i']}/{len(Ks)}: R = {estado['K'] / args.N:.3f} "
              f"(K = {estado['K']}, N = {args.N})   [{sequencia}]")
    else:
        l0 = ' Preparando...'
    if p:
        l1 = (f" Ponto {estado['i']}/{len(pontos)}:  IF TX {p['iftxgain']:g} dB | RF TX {p['rftxgain']:g} dB | "
              f"RF RX {p['rf_gain']:g} dB | {p['freq'] / 1e6:g} MHz")
    else:
        l1 = ' Preparando o enlace...'
    dt = agora - estado['t_fase'] if estado['t_fase'] else 0.0
    if fase == 'espera':
        l2 = f" Aguardando o primeiro quadro certo: {dt:.0f} s de {estado.get('limite', 0):g} s"
    elif fase == 'latencia':
        l2 = f' Medindo o atraso TX->RX ({dt:.1f} s)'
    elif fase == 'descarte':
        falta = max(estado['alvo_descarte'] - c.n, 0) / taxa_amostragem
        l2 = f' Estabilizando o receptor (descarte): faltam {falta:.1f} s'
    elif fase == 'medindo':
        dec, ok = estado['decidido'], estado['ok']
        erros = dec - ok
        bler = f'{erros / dec:.2e}' if dec else '-'
        l2 = (f' Medindo {barra(dec / n_quadros)} {dec}/{n_quadros} quadros   '
              f'erros {erros}   BLER parcial {bler}')
    else:
        l2 = f' {fase}'
    # tempo restante: média dos pontos já feitos (ou a estimativa, enquanto não houver nenhum)
    por_ponto = ((agora - estado['t_pontos']) / feitos if feitos
                 else args.amostras / taxa_amostragem + 1.5) if estado.get('t_pontos') else None
    if por_ponto is not None and feitos < total:
        decorrido_ponto = agora - estado['t_ponto'] if estado['t_ponto'] else 0.0
        falta_t = max(por_ponto * (total - feitos) - decorrido_ponto, 0.0)
        restante = f'falta ~{formatar(falta_t)}'
    else:
        restante = 'falta ~?' if feitos < total else 'concluído'
    l3 = (f' Total {barra(feitos / total)} {feitos}/{total} pontos   '
          f'decorrido {formatar(agora - estado["t_inicio"])}   {restante}')
    u = estado['ultimo']
    l4 = (f" Último ponto: BLER {u['bler']:.2e}, {u['vazao'] / 1e3:.1f} kbit/s, "
          f"{u['erros']} erros em {u['quadros']} quadros (R = {u['R']:.3f})") if u else ' Último ponto: -'
    return [l0, l1, l2, l3, l4]


def monitorar_painel(fim, periodo=0.5):
    """Redesenha o painel no rodapé até 'fim' ser acionado (mesma técnica do qpsk_5g_hw.py)."""
    linhas = hw._linhas()
    while True:
        parar = fim.is_set()                          # se já mandaram parar, desenha uma última vez
        atual_l = hw._linhas()
        redim = ''
        if atual_l != linhas:                         # terminal redimensionado
            for r in range(linhas - hw.ALTURA_PAINEL + 1, linhas + 1):
                redim += f'\033[{r};1H\033[2K'      # apaga o painel antigo
            linhas = atual_l
            redim += f'\033[1;{linhas - hw.ALTURA_PAINEL}r'
        largura = shutil.get_terminal_size((80, 24)).columns - 1   # linha longa quebraria o painel
        t = linhas - hw.ALTURA_PAINEL + 1             # primeira linha do painel
        try:
            textos = linhas_painel()
        except Exception as e:                        # um erro de leitura não pode matar o painel
            textos = [f' (erro ao montar o painel: {e!r})']
        corpo = ''.join(f'\033[{t + 1 + i};1H\033[2K{txt[:largura]}' for i, txt in enumerate(textos))
        sys.stdout.write('\0337' + redim + f'\033[{t};1H\033[2K{"-" * min(60, largura)}' + corpo + '\0338')
        sys.stdout.flush()
        if parar:
            return
        fim.wait(periodo)


colunas = ['N', 'K', 'R'] + list(PADRAO) + ['amostras', 'quadros', 'erros', 'bler', 'vazao_bps',
                                            'baud_rate', 'rajadas', 'maior_rajada']
arquivo = open(args.csv, 'w', newline='')
escritor = csv.DictWriter(arquivo, fieldnames=colunas)
escritor.writeheader()
arquivo.flush()

usar_painel = sys.stdout.isatty() and not args.sem_painel
fim_painel = threading.Event()
if usar_painel:
    hw.ALTURA_PAINEL = ALTURA_PAINEL      # o painel do qpsk_5g_hw tem 4 linhas; este tem 6
    hw.painel_iniciar()                   # reserva o rodapé; apaga a tela, então as mensagens vêm depois
    thread_painel = threading.Thread(target=monitorar_painel, args=(fim_painel,), daemon=True)
    thread_painel.start()
fase_final = 'Encerrada por erro.'
try:
    print(f'resultados em: {args.csv}')
    print('taxas do código polar (R): ' + ', '.join(f'{k / args.N:.3f} (K = {k})' for k in Ks)
          + f'   N = {args.N}')
    print('espera pelo primeiro quadro certo: ' + (f'até {args.espera:g} s' if args.espera else 'desligada'))
    for r_i, K in enumerate(Ks, 1):
        estado.update(fase='abrindo os rádios', r_i=r_i, K=K, i=0, ponto={}, t_ponto=None, t_fase=None)
        montar(K, tentativas=1 if r_i == 1 else TENTATIVAS_RADIO)
        if r_i == 1:
            estimado = len(Ks) * (2 + len(pontos) * (args.amostras / taxa_amostragem + 1.5))
            print(f"{len(Ks)} taxa{'s' if len(Ks) > 1 else ''} x {len(pontos)} pontos, {n_quadros} quadros por ponto, "
                  f'tempo estimado: {formatar(estimado)}'
                  + (' (menos, se --erros encerrar pontos antes)' if args.erros else ''))
        print(f'\n=== taxa {r_i}/{len(Ks)}: N = {tb.N}, K = {tb.K}, R = {tb.R:.3f} ===')
        aplicar(pontos[0])        # o primeiro ponto já vale na partida
        tb.start()
        tb.flowgraph_started.set()
        t_espera = time.monotonic()
        subiu = aguardar_primeiros_quadros(args.espera)
        if subiu:
            print(f'enlace de pé: primeiro quadro certo em {time.monotonic() - t_espera:.1f} s')
        elif args.espera:
            print(f'AVISO: nenhum quadro certo em {args.espera:g} s; varrendo mesmo assim '
                  f'(confira antenas, ganhos e se o TX está no ar)')
        medida = medir_latencia()      # com o enlace de pé, 2 s bastam para ver o maior atraso
        latencia = 2 * medida + 200 if medida is not None else LATENCIA_PADRAO
        f_bom = costas.get_frequency() if medida is not None else 0.0
        print(f'atraso TX->RX medido: {medida} quadros; limite usado: {latencia} quadros '
              f'({latencia * periodo_quadro:.1f} s)')
        estado.setdefault('t_pontos', time.monotonic())    # início da contagem do tempo restante
        for i, ponto in enumerate(pontos, 1):
            estado.update(i=i, ponto=ponto, t_ponto=time.monotonic())
            aplicar(ponto)
            # Se o sinal sumiu no ponto anterior, o Costas pode ter ficado travado numa frequência
            # errada (e não sai de lá nem com sinal bom). Volta para a do último ponto com enlace.
            costas.set_frequency(f_bom)
            estado.update(fase='descarte', alvo_descarte=c.n + DESCARTE, t_fase=time.monotonic())
            esperar(estado['alvo_descarte'])                  # deixa o receptor estabilizar
            a0, t0 = c.n, time.monotonic()
            quadros, ok, rajadas, maior = medir(n_quadros, args.erros)
            a1, t1 = c.n, time.monotonic()
            erros = quadros - ok
            bler = erros / quadros
            if bler < 0.1:
                f_bom = costas.get_frequency()
            vazao = ok * numerador.bits_por_quadro / (quadros * periodo_quadro)   # bits certos por segundo
            baud = (a1 - a0) / (t1 - t0) / tb.get_rx_sps()
            escritor.writerow({'N': tb.N, 'K': tb.K, 'R': round(tb.R, 4), **ponto,
                               'amostras': quadros * amostras_por_quadro, 'quadros': quadros,
                               'erros': erros, 'bler': bler, 'vazao_bps': round(vazao, 1),
                               'baud_rate': round(baud, 1), 'rajadas': rajadas, 'maior_rajada': maior})
            arquivo.flush()                                   # cada ponto vai para o disco na hora
            estado['ultimo'] = {'bler': bler, 'vazao': vazao, 'erros': erros, 'quadros': quadros, 'R': tb.R}
            estado['concluidos'] = (r_i - 1) * len(pontos) + i
            texto = f'BLER {bler:.2e}' if erros else f'BLER < {3 / quadros:.1e} (0 erros; 95% de confiança)'
            print(f'[R {tb.R:.3f}  {i}/{len(pontos)}] {ponto}  ->  {texto}  {vazao / 1e3:.1f} kbit/s, '
                  f'quadros: {quadros}, erros: {erros}, rajadas: {rajadas}')
        if r_i < len(Ks):
            liberar()                 # solta os rádios para o próximo R abri-los
    fase_final = 'Varredura concluída.'
except KeyboardInterrupt:
    print('\nInterrompido.')
    fase_final = 'Interrompida.'
finally:
    liberar(pausa=False)
    arquivo.close()
    estado['fase'] = fase_final
    if usar_painel:
        fim_painel.set()
        thread_painel.join()
        hw.painel_encerrar()              # devolve o terminal ao normal
    print(f'Resultados em {args.csv}')
