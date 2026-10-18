#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: QPSK - 5G (TX HackRF e/ou RX RTL-SDR)
# GNU Radio version: 3.10.12.0
#
# Uso:
#   python3 qpsk_5g_hw.py --modo tx        # so transmite (HackRF)
#   python3 qpsk_5g_hw.py --modo rx        # so recebe (RTL-SDR)
#   python3 qpsk_5g_hw.py                  # os dois no mesmo computador (tx+rx)
#
# Importante: TX e RX precisam rodar esta mesma versao do script. O TX numera
# os quadros e o medidor de BLER do RX usa essa numeracao.

from gnuradio import analog
from gnuradio import blocks
from gnuradio import digital
from gnuradio import fec
from gnuradio import filter
from gnuradio.filter import firdes
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
import numpy as np
from argparse import ArgumentTypeError
from types import SimpleNamespace
import osmosdr
import pmt
import threading
import time
import shutil
try:
    import readline   # historico de comandos com setas cima/baixo (Linux/macOS)
except ImportError:
    pass              # no Windows o proprio console ja oferece o historico no input()

# ---------------------------------------------------------------------------
# Blocos Python embarcados (antes em arquivos separados).
# O codigo das classes e o original; apenas o nome da classe mudou de 'blk'
# para _epy_block_..._blk e cada uma ganhou um "modulo" (SimpleNamespace) com o
# nome que o fluxo ja usava: epy_block_0_0.blk(...) e epy_block_1.blk(...).
# ---------------------------------------------------------------------------

# ---- epy_block_0_0 (de qpsk_5g_claude_epy_block_0_0.py) ----
class _epy_block_0_0_blk(gr.basic_block):
    """
    Find Access Code — invariante às quatro ambiguidades de fase da QPSK.

    Procura o access code no fluxo de LLRs correlacionando, em paralelo, contra
    as quatro versões do código correspondentes às rotações de 0°, 90°, 180° e
    270° da constelação. Ao encontrar, desfaz a rotação no payload antes de
    repassá-lo adiante, de modo que o Costas Loop pode travar em qualquer uma
    das quatro fases sem quebrar o enquadramento.
    """

    def __init__(self, access_code="", payload_len_in_bits=0, tag_key="", threshold=0.9):
        gr.basic_block.__init__(
            self,
            name='Find Access Code',
            in_sig=[np.float32],
            out_sig=[np.float32],
        )
        ac = np.array([2 * int(b) - 1 for b in access_code], dtype=np.float32)
        if ac.size % 2 or payload_len_in_bits % 2:
            raise ValueError('access code e payload devem ter comprimento par')
        self.codes = np.stack([self._rot(ac, k) for k in range(4)])
        self.ac_len = ac.size
        self.pl_len = int(payload_len_in_bits)
        self.frame_len = self.ac_len + self.pl_len
        self.tag_key = pmt.intern(tag_key)
        self.threshold = threshold
        if self.pl_len > 0:
            # Sob os valores padrão (payload 0) o GRC instancia o bloco só para
            # inspecionar as portas; set_output_multiple(0) daria erro ali.
            self.set_output_multiple(self.pl_len)

    @staticmethod
    def _rot(x, k):
        """Aplica k rotações de +90° ao fluxo de LLRs (cada par de LLRs = 1 símbolo)."""
        y = x.reshape(-1, 2).copy()
        for _ in range(k % 4):
            a = y[:, 0].copy()
            y[:, 0] = y[:, 1]
            y[:, 1] = -a
        return y.reshape(-1)

    def forecast(self, noutput_items, ninputs):
        return [self.frame_len] * ninputs

    def general_work(self, input_items, output_items):
        in0 = input_items[0]
        out0 = output_items[0]

        if self.pl_len <= 0:
            return 0
        if in0.size < self.frame_len or out0.size < self.pl_len:
            return 0

        # Busca vetorizada: testa de uma vez todos os offsets em que ainda cabe
        # um quadro inteiro, limitada a um período de quadro por chamada.
        last = min(in0.size - self.frame_len, self.frame_len - 1)
        w = np.lib.stride_tricks.sliding_window_view(in0[:last + self.ac_len], self.ac_len)
        energy = np.abs(w).sum(axis=1)
        metric = (w @ self.codes.T) / np.where(energy > 0.0, energy, np.inf)[:, None]

        idx = np.flatnonzero(metric.max(axis=1) >= self.threshold)
        if idx.size == 0:
            self.consume(0, last + 1)   # descarta o que já foi testado, guarda a cauda
            return 0

        n = int(idx[0])                      # primeiro quadro do bloco
        k = int(np.argmax(metric[n]))        # qual das 4 rotações casou
        payload = in0[n + self.ac_len: n + self.frame_len]
        out0[:self.pl_len] = self._rot(payload, -k)   # desfaz a rotação
        self.consume(0, n + self.frame_len)
        self.add_item_tag(0, self.nitems_written(0), self.tag_key, pmt.from_long(self.pl_len))
        return self.pl_len


epy_block_0_0 = SimpleNamespace(blk=_epy_block_0_0_blk)


# ---- epy_block_1: Medidor de BLER (RX) ----
"""
Medidor de BLER por numeracao de quadros.

O TX escreve um numero sequencial (0, 1, 2, ...) nos primeiros 'bits_numero'
bits de dados de cada quadro (bloco 'Numerador de quadros', mais abaixo).
Este medidor fica na saida do CRC, que so deixa passar quadros corretos, e le
esse numero. Se chegam os quadros 7 e 10, os quadros 8 e 9 se perderam: 2 erros.

Um quadro so entra na conta (como certo ou como perdido) depois de ter saido
do receptor; por isso o numero de erros nunca diminui. Quadros que o receptor
nem chegou a detectar tambem contam como erro.

Qualquer mensagem na porta 'zerar' recomeca a contagem.
"""


class _epy_block_1_blk(gr.sync_block):
    def __init__(self, bits_por_quadro=480, bits_numero=32):
        gr.sync_block.__init__(
            self,
            name='Medidor de BLER',
            in_sig=[np.uint8],
            out_sig=None,
        )
        self.bits_por_quadro = int(bits_por_quadro)
        self.bits_numero = int(bits_numero)
        self._modulo = 1 << self.bits_numero
        self._salto_max = self._modulo >> 8      # com 32 bits: 2**24 quadros (~6 h no ar)
        self._pesos = 1 << np.arange(self.bits_numero - 1, -1, -1, dtype=np.int64)
        self._sobra = np.zeros(0, dtype=np.uint8)   # pedaco de quadro que chegou incompleto
        self._trava = threading.Lock()              # resumo() e chamado de outra thread
        self._zerar(None)
        self.message_port_register_in(pmt.intern('zerar'))
        self.set_msg_handler(pmt.intern('zerar'), self._zerar)

    def _zerar(self, msg):
        with self._trava:
            self.ultimo = None       # numero do ultimo quadro correto
            self.n_ok = 0            # quadros corretos
            self.n_total = 0         # quadros corretos + perdidos
            self.t_ultimo = None     # instante em que chegou o ultimo quadro correto
            self.incoerentes = 0     # saltos absurdos na numeracao

    def _registrar(self, numero):
        if self.ultimo is not None:
            salto = (numero - self.ultimo) % self._modulo
            if salto == 0:
                return                       # quadro repetido: ignora
            if salto <= self._salto_max:
                self.n_total += salto        # (salto - 1) quadros perdidos + este
                self.n_ok += 1
                self.ultimo = numero
                return
            # Salto absurdo (a numeracao voltou: o TX foi reiniciado).
            # Os quadros do intervalo sao desconhecidos; recomeca deste.
            self.incoerentes += 1
        self.ultimo = numero
        self.n_total += 1
        self.n_ok += 1

    def work(self, input_items, output_items):
        L = self.bits_por_quadro
        bits = np.concatenate((self._sobra, input_items[0]))
        n = bits.size // L
        self._sobra = bits[n * L:].copy()
        if n:
            cabecalhos = bits[:n * L].reshape(n, L)[:, :self.bits_numero]
            numeros = cabecalhos.astype(np.int64) @ self._pesos
            with self._trava:
                for numero in numeros.tolist():
                    self._registrar(numero)
                self.t_ultimo = time.monotonic()
        return len(input_items[0])

    def resumo(self):
        with self._trava:
            total, ok = self.n_total, self.n_ok
            t, incoerentes = self.t_ultimo, self.incoerentes
        if total == 0:
            return 'aguardando quadros...'
        if 2 * incoerentes > ok:
            return 'numeracao nao confere: atualize o script no TX e digite reset'
        erros = total - ok
        texto = f'{erros / total:.2e}   ({erros} erros em {total} quadros)'
        parado = time.monotonic() - t
        if parado > 1.0:
            texto += f'   [nenhum quadro correto ha {parado:.0f} s]'
        return texto


epy_block_1 = SimpleNamespace(blk=_epy_block_1_blk)


# ---- epy_block_2: Numerador de quadros (TX) ----
class _epy_block_2_blk(gr.sync_block):
    """
    Numerador de quadros.

    Escreve o numero do quadro (0, 1, 2, ..., MSB primeiro) nos primeiros
    'bits_numero' bits de cada quadro de 'bits_por_quadro' bits de dados.
    O resto do quadro continua aleatorio. O Medidor de BLER do RX usa esse
    numero para saber quais quadros se perderam.
    """

    def __init__(self, bits_por_quadro=480, bits_numero=32):
        gr.sync_block.__init__(
            self,
            name='Numerador de quadros',
            in_sig=[np.uint8],
            out_sig=[np.uint8],
        )
        if bits_numero > bits_por_quadro:
            raise ValueError('o numero do quadro nao cabe no quadro')
        self.bits_por_quadro = int(bits_por_quadro)
        self.bits_numero = int(bits_numero)

    def work(self, input_items, output_items):
        out = output_items[0]
        n = len(out)
        out[:] = input_items[0][:n]
        i = self.nitems_written(0) + np.arange(n, dtype=np.int64)   # indice de cada bit no fluxo
        pos = i % self.bits_por_quadro                              # posicao do bit no quadro
        cab = pos < self.bits_numero                                # bits reservados ao numero
        numero = (i[cab] // self.bits_por_quadro) % (1 << self.bits_numero)
        out[cab] = (numero >> (self.bits_numero - 1 - pos[cab])) & 1
        return n


epy_block_2 = SimpleNamespace(blk=_epy_block_2_blk)

MODOS = ('tx', 'rx', 'tx+rx')


class qpsk_5g_hw(gr.top_block):

    def __init__(self, modo='tx+rx', tx_args='hackrf=0', rx_args='rtl=0'):
        gr.top_block.__init__(self, "QPSK - 5G (" + modo + ")", catch_exceptions=True)
        if modo not in MODOS:
            raise ValueError(f'modo invalido: {modo!r} (use: {", ".join(MODOS)})')
        self.flowgraph_started = threading.Event()
        self.modo = modo
        self.tem_tx = 'tx' in modo.split('+')
        self.tem_rx = 'rx' in modo.split('+')

        ##################################################
        # Variables
        ##################################################
        self.bit_rate = bit_rate = 800e3
        self.baud_rate = baud_rate = bit_rate / 2
        self.tx_sps = tx_sps = 24
        self.rx_sps = rx_sps = 6
        self.tx_samp_rate = tx_samp_rate = baud_rate * tx_sps
        self.rx_samp_rate = rx_samp_rate = baud_rate * rx_sps
        self.RelSeq = RelSeq = np.array([0, 1, 2, 4, 8, 16, 32, 3, 5, 64, 9, 6, 17, 10, 18, 128, 12, 33, 65, 20, 256, 34, 24, 36, 7, 129, 66, 512, 11, 40, 68, 130, 19, 13, 48, 14, 72, 257, 21, 132, 35, 258, 26, 513, 80, 37, 25, 22, 136, 260, 264, 38, 514, 96, 67, 41, 144, 28, 69, 42, 516, 49, 74, 272, 160, 520, 288, 528, 192, 544, 70, 44, 131, 81, 50, 73, 15, 320, 133, 52, 23, 134, 384, 76, 137, 82, 56, 27, 97, 39, 259, 84, 138, 145, 261, 29, 43, 98, 515, 88, 140, 30, 146, 71, 262, 265, 161, 576, 45, 100, 640, 51, 148, 46, 75, 266, 273, 517, 104, 162, 53, 193, 152, 77, 164, 768, 268, 274, 518, 54, 83, 57, 521, 112, 135, 78, 289, 194, 85, 276, 522, 58, 168, 139, 99, 86, 60, 280, 89, 290, 529, 524, 196, 141, 101, 147, 176, 142, 530, 321, 31, 200, 90, 545, 292, 322, 532, 263, 149, 102, 105, 304, 296, 163, 92, 47, 267, 385, 546, 324, 208, 386, 150, 153, 165, 106, 55, 328, 536, 577, 548, 113, 154, 79, 269, 108, 578, 224, 166, 519, 552, 195, 270, 641, 523, 275, 580, 291, 59, 169, 560, 114, 277, 156, 87, 197, 116, 170, 61, 531, 525, 642, 281, 278, 526, 177, 293, 388, 91, 584, 769, 198, 172, 120, 201, 336, 62, 282, 143, 103, 178, 294, 93, 644, 202, 592, 323, 392, 297, 770, 107, 180, 151, 209, 284, 648, 94, 204, 298, 400, 608, 352, 325, 533, 155, 210, 305, 547, 300, 109, 184, 534, 537, 115, 167, 225, 326, 306, 772, 157, 656, 329, 110, 117, 212, 171, 776, 330, 226, 549, 538, 387, 308, 216, 416, 271, 279, 158, 337, 550, 672, 118, 332, 579, 540, 389, 173, 121, 553, 199, 784, 179, 228, 338, 312, 704, 390, 174, 554, 581, 393, 283, 122, 448, 353, 561, 203, 63, 340, 394, 527, 582, 556, 181, 295, 285, 232, 124, 205, 182, 643, 562, 286, 585, 299, 354, 211, 401, 185, 396, 344, 586, 645, 593, 535, 240, 206, 95, 327, 564, 800, 402, 356, 307, 301, 417, 213, 568, 832, 588, 186, 646, 404, 227, 896, 594, 418, 302, 649, 771, 360, 539, 111, 331, 214, 309, 188, 449, 217, 408, 609, 596, 551, 650, 229, 159, 420, 310, 541, 773, 610, 657, 333, 119, 600, 339, 218, 368, 652, 230, 391, 313, 450, 542, 334, 233, 555, 774, 175, 123, 658, 612, 341, 777, 220, 314, 424, 395, 673, 583, 355, 287, 183, 234, 125, 557, 660, 616, 342, 316, 241, 778, 563, 345, 452, 397, 403, 207, 674, 558, 785, 432, 357, 187, 236, 664, 624, 587, 780, 705, 126, 242, 565, 398, 346, 456, 358, 405, 303, 569, 244, 595, 189, 566, 676, 361, 706, 589, 215, 786, 647, 348, 419, 406, 464, 680, 801, 362, 590, 409, 570, 788, 597, 572, 219, 311, 708, 598, 601, 651, 421, 792, 802, 611, 602, 410, 231, 688, 653, 248, 369, 190, 364, 654, 659, 335, 480, 315, 221, 370, 613, 422, 425, 451, 614, 543, 235, 412, 343, 372, 775, 317, 222, 426, 453, 237, 559, 833, 804, 712, 834, 661, 808, 779, 617, 604, 433, 720, 816, 836, 347, 897, 243, 662, 454, 318, 675, 618, 898, 781, 376, 428, 665, 736, 567, 840, 625, 238, 359, 457, 399, 787, 591, 678, 434, 677, 349, 245, 458, 666, 620, 363, 127, 191, 782, 407, 436, 626, 571, 465, 681, 246, 707, 350, 599, 668, 790, 460, 249, 682, 573, 411, 803, 789, 709, 365, 440, 628, 689, 374, 423, 466, 793, 250, 371, 481, 574, 413, 603, 366, 468, 655, 900, 805, 615, 684, 710, 429, 794, 252, 373, 605, 848, 690, 713, 632, 482, 806, 427, 904, 414, 223, 663, 692, 835, 619, 472, 455, 796, 809, 714, 721, 837, 716, 864, 810, 606, 912, 722, 696, 377, 435, 817, 319, 621, 812, 484, 430, 838, 667, 488, 239, 378, 459, 622, 627, 437, 380, 818, 461, 496, 669, 679, 724, 841, 629, 351, 467, 438, 737, 251, 462, 442, 441, 469, 247, 683, 842, 738, 899, 670, 783, 849, 820, 728, 928, 791, 367, 901, 630, 685, 844, 633, 711, 253, 691, 824, 902, 686, 740, 850, 375, 444, 470, 483, 415, 485, 905, 795, 473, 634, 744, 852, 960, 865, 693, 797, 906, 715, 807, 474, 636, 694, 254, 717, 575, 913, 798, 811, 379, 697, 431, 607, 489, 866, 723, 486, 908, 718, 813, 476, 856, 839, 725, 698, 914, 752, 868, 819, 814, 439, 929, 490, 623, 671, 739, 916, 463, 843, 381, 497, 930, 821, 726, 961, 872, 492, 631, 729, 700, 443, 741, 845, 920, 382, 822, 851, 730, 498, 880, 742, 445, 471, 635, 932, 687, 903, 825, 500, 846, 745, 826, 732, 446, 962, 936, 475, 853, 867, 637, 907, 487, 695, 746, 828, 753, 854, 857, 504, 799, 255, 964, 909, 719, 477, 915, 638, 748, 944, 869, 491, 699, 754, 858, 478, 968, 383, 910, 815, 976, 870, 917, 727, 493, 873, 701, 931, 756, 860, 499, 731, 823, 922, 874, 918, 502, 933, 743, 760, 881, 494, 702, 921, 501, 876, 847, 992, 447, 733, 827, 934, 882, 937, 963, 747, 505, 855, 924, 734, 829, 965, 938, 884, 506, 749, 945, 966, 755, 859, 940, 830, 911, 871, 639, 888, 479, 946, 750, 969, 508, 861, 757, 970, 919, 875, 862, 758, 948, 977, 923, 972, 761, 877, 952, 495, 703, 935, 978, 883, 762, 503, 925, 878, 735, 993, 885, 939, 994, 980, 926, 764, 941, 967, 886, 831, 947, 507, 889, 984, 751, 942, 996, 971, 890, 509, 949, 973, 1000, 892, 950, 863, 759, 1008, 510, 979, 953, 763, 974, 954, 879, 981, 982, 927, 995, 765, 956, 887, 985, 997, 986, 943, 891, 998, 766, 511, 988, 1001, 951, 1002, 893, 975, 894, 1009, 955, 1004, 1010, 957, 983, 958, 987, 1012, 999, 1016, 767, 989, 1003, 990, 1005, 959, 1011, 1013, 895, 1006, 1014, 1017, 1018, 991, 1020, 1007, 1015, 1019, 1021, 1022, 1023])
        self.N = N = 1024
        self.K = K = 512
        self.frozen_val = frozen_val = np.zeros(N-K, dtype=int)
        self.frozen_pos = frozen_pos = np.sort(RelSeq[RelSeq < N][:N-K])
        self.tx_rrc_taps = tx_rrc_taps = firdes.root_raised_cosine(tx_sps, tx_samp_rate, baud_rate, 0.25, (11*tx_sps))
        self.rx_rrc_taps = rx_rrc_taps = firdes.root_raised_cosine(rx_sps, rx_samp_rate, baud_rate, 0.25, (11*rx_sps))
        self.tx_taps = tx_taps = np.array(tx_rrc_taps)
        self.rx_taps = rx_taps = np.array(rx_rrc_taps)
        self.access_code = access_code = "11100001010110101110100010010011"
        self.sync_len = sync_len = len(access_code)
        self.bits_numero = bits_numero = 32   # numero do quadro, no inicio dos dados de cada quadro
        self.qpsk = qpsk = digital.constellation_qpsk().base()
        self.qpsk.set_npwr(1.0)
        self.freq = freq = 670e6
        self.rftxgain = rftxgain = 30
        self.iftxgain = iftxgain = 30
        self.rf_gain = rf_gain = 30
        self.vazao_probe = vazao_probe = 0
        self.samp_rate_probe = samp_rate_probe = 0
        self.baud_rate_probe = baud_rate_probe = 0
        self.bler_probe = bler_probe = 'aguardando quadros...'
        self.PC_enc = PC_enc = fec.polar_encoder.make(N,K, frozen_pos, frozen_val, False)
        self.PC_dec = PC_dec = fec.polar_decoder_sc.make(N,K, frozen_pos, frozen_val)

        ##################################################
        # Blocks e conexoes (so do lado pedido em 'modo')
        ##################################################
        if self.tem_tx:
            self._montar_tx(tx_args)
        if self.tem_rx:
            self._montar_rx(rx_args)

    def _montar_tx(self, tx_args):
        ##################################################
        # Blocks - TX
        ##################################################
        self.analog_random_uniform_source_x_0 = analog.random_uniform_source_b(0, 256, 42)
        self.blocks_unpack_k_bits_bb_0 = blocks.unpack_k_bits_bb(8)
        self.epy_block_2 = epy_block_2.blk(bits_por_quadro=self.K-32, bits_numero=self.bits_numero)
        self.blocks_stream_to_tagged_stream_0 = blocks.stream_to_tagged_stream(gr.sizeof_char, 1, (self.K-32), "quadro")
        self.digital_crc32_bb_0_0 = digital.crc32_bb(False, "quadro", False)
        self.fec_extended_tagged_encoder_0 = fec.extended_tagged_encoder(encoder_obj_list=self.PC_enc, puncpat='11', lentagname="quadro", mtu=1500)
        self.blocks_vector_source_x_0 = blocks.vector_source_b((0xE1, 0x5A, 0xE8, 0x93), True, 1, [])
        self.blocks_unpack_k_bits_bb_0_0 = blocks.unpack_k_bits_bb(8)
        self.blocks_stream_mux_0 = blocks.stream_mux(gr.sizeof_char*1, (32, self.N))
        self.blocks_pack_k_bits_bb_1 = blocks.pack_k_bits_bb(2)
        self.digital_constellation_encoder_bc_0 = digital.constellation_encoder_bc(self.qpsk)
        self.interp_fir_filter_xxx_0 = filter.interp_fir_filter_ccc(self.tx_sps, self.tx_taps)
        self.interp_fir_filter_xxx_0.declare_sample_delay(0)
        self.osmosdr_sink_0 = osmosdr.sink(
            args="numchan=" + str(1) + " " + tx_args
        )
        self.osmosdr_sink_0.set_time_unknown_pps(osmosdr.time_spec_t())
        self.osmosdr_sink_0.set_sample_rate(self.tx_samp_rate)
        self.osmosdr_sink_0.set_center_freq(self.freq, 0)
        self.osmosdr_sink_0.set_freq_corr(0, 0)
        self.osmosdr_sink_0.set_gain(self.rftxgain, 0)
        self.osmosdr_sink_0.set_if_gain(self.iftxgain, 0)
        self.osmosdr_sink_0.set_bb_gain(0, 0)
        self.osmosdr_sink_0.set_antenna('', 0)
        self.osmosdr_sink_0.set_bandwidth(0, 0)

        ##################################################
        # Connections - TX
        ##################################################
        self.connect((self.analog_random_uniform_source_x_0, 0), (self.blocks_unpack_k_bits_bb_0, 0))
        self.connect((self.blocks_unpack_k_bits_bb_0, 0), (self.epy_block_2, 0))
        self.connect((self.epy_block_2, 0), (self.blocks_stream_to_tagged_stream_0, 0))
        self.connect((self.blocks_stream_to_tagged_stream_0, 0), (self.digital_crc32_bb_0_0, 0))
        self.connect((self.digital_crc32_bb_0_0, 0), (self.fec_extended_tagged_encoder_0, 0))
        self.connect((self.fec_extended_tagged_encoder_0, 0), (self.blocks_stream_mux_0, 1))
        self.connect((self.blocks_vector_source_x_0, 0), (self.blocks_unpack_k_bits_bb_0_0, 0))
        self.connect((self.blocks_unpack_k_bits_bb_0_0, 0), (self.blocks_stream_mux_0, 0))
        self.connect((self.blocks_stream_mux_0, 0), (self.blocks_pack_k_bits_bb_1, 0))
        self.connect((self.blocks_pack_k_bits_bb_1, 0), (self.digital_constellation_encoder_bc_0, 0))
        self.connect((self.digital_constellation_encoder_bc_0, 0), (self.interp_fir_filter_xxx_0, 0))
        self.connect((self.interp_fir_filter_xxx_0, 0), (self.osmosdr_sink_0, 0))

    def _montar_rx(self, rx_args):
        ##################################################
        # Blocks - RX
        ##################################################
        self.osmosdr_source_0 = osmosdr.source(
            args="numchan=" + str(1) + " " + rx_args
        )
        self.osmosdr_source_0.set_time_unknown_pps(osmosdr.time_spec_t())
        self.osmosdr_source_0.set_sample_rate(self.rx_samp_rate)
        self.osmosdr_source_0.set_center_freq(self.freq, 0)
        self.osmosdr_source_0.set_freq_corr(0, 0)
        self.osmosdr_source_0.set_dc_offset_mode(0, 0)
        self.osmosdr_source_0.set_iq_balance_mode(0, 0)
        self.osmosdr_source_0.set_gain_mode(False, 0)
        self.osmosdr_source_0.set_gain(self.rf_gain, 0)
        self.osmosdr_source_0.set_if_gain(0, 0)
        self.osmosdr_source_0.set_bb_gain(0, 0)
        self.osmosdr_source_0.set_antenna('', 0)
        self.osmosdr_source_0.set_bandwidth(0, 0)
        self.interp_fir_filter_xxx_1 = filter.interp_fir_filter_ccc(1, self.rx_taps/self.rx_sps)
        self.interp_fir_filter_xxx_1.declare_sample_delay(0)
        self.analog_agc_xx_0 = analog.agc_cc((1e-4), 1.0, 1.0, 1e3)
        self.digital_symbol_sync_xx_0 = digital.symbol_sync_cc(
            digital.TED_GARDNER,
            self.rx_sps,
            0.045,
            1.0,
            1.0,
            0.02,
            1,
            digital.constellation_bpsk().base(),
            digital.IR_MMSE_8TAP,
            128,
            [])
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(0.5)
        self.digital_costas_loop_cc_0 = digital.costas_loop_cc((2*np.pi/200), 4, False)
        self.digital_constellation_soft_decoder_cf_0 = digital.constellation_soft_decoder_cf(self.qpsk, -1)
        self.epy_block_0_0 = epy_block_0_0.blk(access_code=self.access_code, payload_len_in_bits=self.N, tag_key="quadro", threshold=0.9)
        self.fec_extended_tagged_decoder_0 = fec.extended_tagged_decoder(decoder_obj_list=self.PC_dec, ann=None, puncpat='11', integration_period=10000, lentagname="quadro", mtu=1500)
        self.digital_crc32_bb_0 = digital.crc32_bb(True, "quadro", False)

        ##################################################
        # Blocks - medidores
        ##################################################
        self.epy_block_1 = epy_block_1.blk(bits_por_quadro=self.K-32, bits_numero=self.bits_numero)
        self.blocks_probe_rate_0_1 = blocks.probe_rate(gr.sizeof_char*1, 500.0, 0.15, "vazão")
        self.blocks_probe_rate_0_0 = blocks.probe_rate(gr.sizeof_gr_complex*1, 500.0, 0.15, "samp rate (rtl)")
        self.blocks_probe_rate_0 = blocks.probe_rate(gr.sizeof_gr_complex*1, 500.0, 0.15, "baud rate")
        self._iniciar_probe(self.blocks_probe_rate_0_1.rate, self.set_vazao_probe)
        self._iniciar_probe(self.blocks_probe_rate_0_0.rate, self.set_samp_rate_probe)
        self._iniciar_probe(self.blocks_probe_rate_0.rate, self.set_baud_rate_probe)
        self._iniciar_probe(self.epy_block_1.resumo, self.set_bler_probe)

        ##################################################
        # Connections - RX
        ##################################################
        self.connect((self.osmosdr_source_0, 0), (self.blocks_probe_rate_0_0, 0))
        self.connect((self.osmosdr_source_0, 0), (self.interp_fir_filter_xxx_1, 0))
        self.connect((self.interp_fir_filter_xxx_1, 0), (self.analog_agc_xx_0, 0))
        self.connect((self.analog_agc_xx_0, 0), (self.digital_symbol_sync_xx_0, 0))
        self.connect((self.digital_symbol_sync_xx_0, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.digital_costas_loop_cc_0, 0))
        self.connect((self.digital_costas_loop_cc_0, 0), (self.blocks_probe_rate_0, 0))
        self.connect((self.digital_costas_loop_cc_0, 0), (self.digital_constellation_soft_decoder_cf_0, 0))
        self.connect((self.digital_constellation_soft_decoder_cf_0, 0), (self.epy_block_0_0, 0))
        self.connect((self.epy_block_0_0, 0), (self.fec_extended_tagged_decoder_0, 0))
        self.connect((self.fec_extended_tagged_decoder_0, 0), (self.digital_crc32_bb_0, 0))
        self.connect((self.digital_crc32_bb_0, 0), (self.blocks_probe_rate_0_1, 0))
        self.connect((self.digital_crc32_bb_0, 0), (self.epy_block_1, 0))

    def _iniciar_probe(self, leitura, setter, periodo=0.5):
        """Thread que, a cada 'periodo' s, le um medidor e grava o valor na variavel."""
        def _loop():
            self.flowgraph_started.wait()
            while True:
                setter(leitura())
                time.sleep(periodo)
        threading.Thread(target=_loop, daemon=True).start()

    def _atualizar_taxas(self):
        """Recalcula taxas de amostragem e filtros RRC apos mudar baud/sps."""
        self.tx_samp_rate = self.baud_rate * self.tx_sps
        self.rx_samp_rate = self.baud_rate * self.rx_sps
        self.set_tx_rrc_taps(firdes.root_raised_cosine(self.tx_sps, self.tx_samp_rate, self.baud_rate, 0.25, (11*self.tx_sps)))
        self.set_rx_rrc_taps(firdes.root_raised_cosine(self.rx_sps, self.rx_samp_rate, self.baud_rate, 0.25, (11*self.rx_sps)))
        if self.tem_tx:
            self.osmosdr_sink_0.set_sample_rate(self.tx_samp_rate)
        if self.tem_rx:
            self.osmosdr_source_0.set_sample_rate(self.rx_samp_rate)

    # ---- taxas ----
    def get_bit_rate(self):
        return self.bit_rate

    def set_bit_rate(self, bit_rate):
        self.bit_rate = bit_rate
        self.set_baud_rate(self.bit_rate / 2)

    def get_baud_rate(self):
        return self.baud_rate

    def set_baud_rate(self, baud_rate):
        self.baud_rate = baud_rate
        self._atualizar_taxas()

    def get_tx_sps(self):
        return self.tx_sps

    def set_tx_sps(self, tx_sps):
        self.tx_sps = tx_sps
        self._atualizar_taxas()

    def get_rx_sps(self):
        return self.rx_sps

    def set_rx_sps(self, rx_sps):
        self.rx_sps = rx_sps
        self._atualizar_taxas()
        if self.tem_rx:
            self.digital_symbol_sync_xx_0.set_sps(self.rx_sps)

    def get_tx_samp_rate(self):
        return self.tx_samp_rate

    def get_rx_samp_rate(self):
        return self.rx_samp_rate

    # ---- codigo de canal ----
    def get_RelSeq(self):
        return self.RelSeq

    def set_RelSeq(self, RelSeq):
        self.RelSeq = RelSeq
        self.set_frozen_pos(np.sort(self.RelSeq[self.RelSeq < self.N][:self.N-self.K]))

    def get_N(self):
        return self.N

    def set_N(self, N):
        self.N = N
        self.set_frozen_pos(np.sort(self.RelSeq[self.RelSeq < self.N][:self.N-self.K]))
        self.set_frozen_val(np.zeros(self.N-self.K, dtype=int))

    def get_K(self):
        return self.K

    def set_K(self, K):
        self.K = K
        self.set_frozen_pos(np.sort(self.RelSeq[self.RelSeq < self.N][:self.N-self.K]))
        self.set_frozen_val(np.zeros(self.N-self.K, dtype=int))
        if self.tem_tx:
            self.blocks_stream_to_tagged_stream_0.set_packet_len((self.K-32))
            self.blocks_stream_to_tagged_stream_0.set_packet_len_pmt((self.K-32))
            self.epy_block_2.bits_por_quadro = self.K-32
        if self.tem_rx:
            self.epy_block_1.bits_por_quadro = self.K-32

    def get_frozen_val(self):
        return self.frozen_val

    def set_frozen_val(self, frozen_val):
        self.frozen_val = frozen_val

    def get_frozen_pos(self):
        return self.frozen_pos

    def set_frozen_pos(self, frozen_pos):
        self.frozen_pos = frozen_pos

    def get_PC_enc(self):
        return self.PC_enc

    def set_PC_enc(self, PC_enc):
        self.PC_enc = PC_enc

    def get_PC_dec(self):
        return self.PC_dec

    def set_PC_dec(self, PC_dec):
        self.PC_dec = PC_dec

    # ---- filtros ----
    def get_tx_rrc_taps(self):
        return self.tx_rrc_taps

    def set_tx_rrc_taps(self, tx_rrc_taps):
        self.tx_rrc_taps = tx_rrc_taps
        self.set_tx_taps(np.array(self.tx_rrc_taps))

    def get_rx_rrc_taps(self):
        return self.rx_rrc_taps

    def set_rx_rrc_taps(self, rx_rrc_taps):
        self.rx_rrc_taps = rx_rrc_taps
        self.set_rx_taps(np.array(self.rx_rrc_taps))

    def get_tx_taps(self):
        return self.tx_taps

    def set_tx_taps(self, tx_taps):
        self.tx_taps = tx_taps
        if self.tem_tx:
            self.interp_fir_filter_xxx_0.set_taps(self.tx_taps)

    def get_rx_taps(self):
        return self.rx_taps

    def set_rx_taps(self, rx_taps):
        self.rx_taps = rx_taps
        if self.tem_rx:
            self.interp_fir_filter_xxx_1.set_taps(self.rx_taps/self.rx_sps)

    # ---- sincronismo / constelacao ----
    def get_access_code(self):
        return self.access_code

    def set_access_code(self, access_code):
        self.access_code = access_code
        self.set_sync_len(len(self.access_code))

    def get_sync_len(self):
        return self.sync_len

    def set_sync_len(self, sync_len):
        self.sync_len = sync_len

    def get_qpsk(self):
        return self.qpsk

    def set_qpsk(self, qpsk):
        self.qpsk = qpsk
        if self.tem_tx:
            self.digital_constellation_encoder_bc_0.set_constellation(self.qpsk)
        if self.tem_rx:
            self.digital_constellation_soft_decoder_cf_0.set_constellation(self.qpsk)

    # ---- radio ----
    def get_freq(self):
        return self.freq

    def set_freq(self, freq):
        self.freq = freq
        if self.tem_tx:
            self.osmosdr_sink_0.set_center_freq(self.freq, 0)
        if self.tem_rx:
            self.osmosdr_source_0.set_center_freq(self.freq, 0)

    def get_rftxgain(self):
        return self.rftxgain

    def set_rftxgain(self, rftxgain):
        self.rftxgain = rftxgain
        if self.tem_tx:
            self.osmosdr_sink_0.set_gain(self.rftxgain, 0)

    def get_iftxgain(self):
        return self.iftxgain

    def set_iftxgain(self, iftxgain):
        self.iftxgain = iftxgain
        if self.tem_tx:
            self.osmosdr_sink_0.set_if_gain(self.iftxgain, 0)

    def get_rf_gain(self):
        return self.rf_gain

    def set_rf_gain(self, rf_gain):
        self.rf_gain = rf_gain
        if self.tem_rx:
            self.osmosdr_source_0.set_gain(self.rf_gain, 0)

    # ---- medidores ----
    def get_vazao_probe(self):
        return self.vazao_probe

    def set_vazao_probe(self, vazao_probe):
        self.vazao_probe = vazao_probe

    def get_samp_rate_probe(self):
        return self.samp_rate_probe

    def set_samp_rate_probe(self, samp_rate_probe):
        self.samp_rate_probe = samp_rate_probe

    def get_baud_rate_probe(self):
        return self.baud_rate_probe

    def set_baud_rate_probe(self, baud_rate_probe):
        self.baud_rate_probe = baud_rate_probe

    def get_bler_probe(self):
        return self.bler_probe

    def set_bler_probe(self, bler_probe):
        self.bler_probe = bler_probe


# Fatores ajustaveis no terminal; main() reduz esta lista conforme o modo.
FATORES = ('freq', 'rftxgain', 'iftxgain', 'rf_gain')


def fatores_do_modo(modo):
    fatores = ['freq']
    if 'tx' in modo.split('+'):
        fatores += ['rftxgain', 'iftxgain']
    if 'rx' in modo.split('+'):
        fatores += ['rf_gain']
    return tuple(fatores)


def ler_args(argv=None):
    parser = ArgumentParser(
        description='QPSK - 5G: TX (HackRF) e/ou RX (RTL-SDR) num unico arquivo.',
        epilog='Com o link rodando, digite no terminal, por exemplo: '
               'rftxgain 20   (varios de uma vez: rftxgain 20, rf_gain 40; '
               'reset zera a BLER; clear limpa o terminal). '
               'ENTER vazio encerra.')
    parser.add_argument('--modo', choices=MODOS, default='tx+rx',
                        help='o que montar: so transmissor, so receptor ou os dois (padrao: tx+rx)')
    parser.add_argument('--freq', type=eng_float, default=None, metavar='HZ',
                        help='frequencia central (padrao do flowgraph: 670M)')
    parser.add_argument('--rftxgain', type=eng_float, default=None, metavar='dB',
                        help='ganho RF do TX (padrao do flowgraph: 30)')
    parser.add_argument('--iftxgain', type=eng_float, default=None, metavar='dB',
                        help='ganho IF do TX (padrao do flowgraph: 30)')
    parser.add_argument('--rf_gain', type=eng_float, default=None, metavar='dB',
                        help='ganho RF do RX (padrao do flowgraph: 30)')
    parser.add_argument('--tx_args', default='hackrf=0', metavar='ARGS',
                        help='argumentos osmosdr do TX (padrao: hackrf=0)')
    parser.add_argument('--rx_args', default='rtl=0', metavar='ARGS',
                        help='argumentos osmosdr do RX (padrao: rtl=0)')
    return parser.parse_args(argv)


COMANDOS_RESET = ('reset', 'zerar', 'r')
COMANDOS_CLEAR = ('clear', 'limpar')


def aplicar_comando(tb, linha):
    """Muda fatores do canal com a simulacao rodando.
    Ex.: 'noise_voltage 0.5'  ou  'noise_voltage 0.5, freq_offset 0.001'
    (tambem aceita '=': 'noise_voltage=0.5,freq_offset=0.001').
    'reset' zera a BLER; 'clear' limpa o terminal (o painel fica)."""
    uso = ('uso: <fator> <valor> [, <fator> <valor> ...]   fatores: ' + ', '.join(FATORES)
           + '   |   reset: zera a BLER   |   clear: limpa o terminal')
    acoes = []   # (tipo, nome, valor), na ordem digitada
    for trecho in linha.split(','):
        partes = trecho.replace('=', ' ').split()
        if not partes:
            continue
        if len(partes) == 1 and partes[0].lower() in COMANDOS_RESET:
            acoes.append(('reset', None, None))
            continue
        if len(partes) == 1 and partes[0].lower() in COMANDOS_CLEAR:
            acoes.append(('clear', None, None))
            continue
        if len(partes) % 2:
            print(uso)
            return
        for nome, texto in zip(partes[0::2], partes[1::2]):
            if nome not in FATORES:
                print(f'fator desconhecido: {nome}   (use: {", ".join(FATORES)})')
                return
            try:
                acoes.append(('set', nome, eng_float(texto)))
            except (ArgumentTypeError, ValueError) as e:
                print(e)
                return
    if not acoes:
        print(uso)
        return
    mudou = False
    for tipo, nome, valor in acoes:       # so aplica depois de validar tudo
        if tipo == 'reset':
            # usa a porta de mensagem 'zerar' do Medidor de BLER
            if tb.tem_rx:
                tb.epy_block_1._post(pmt.intern('zerar'), pmt.PMT_T)
                print('BLER zerada')
            else:
                print('reset so vale com RX ativo (modo atual: ' + tb.modo + ')')
        elif tipo == 'clear':
            limpar_terminal()
        else:
            getattr(tb, 'set_' + nome)(valor)
            mudou = True
    if mudou:                             # mostra todos os fatores, inclusive os nao alterados
        print('Radio: ' + ', '.join(f'{n} = {getattr(tb, "get_" + n)()}' for n in FATORES))


ALTURA_PAINEL = 4   # linhas reservadas no rodape do terminal


def _linhas():
    return shutil.get_terminal_size((80, 24)).lines


def painel_iniciar():
    """Reserva as ultimas linhas do terminal; o prompt fica logo acima do painel."""
    topo = _linhas() - ALTURA_PAINEL
    sys.stdout.write('\033[2J'                  # limpa a tela
                     f'\033[1;{topo}r'           # area de rolagem acima do painel
                     f'\033[{topo};1H')          # cursor na ultima linha da area de rolagem
    sys.stdout.flush()


def limpar_terminal():
    """Limpa a area de rolagem (como o 'clear' do terminal), mantendo o painel."""
    topo = _linhas() - ALTURA_PAINEL
    sys.stdout.write(''.join(f'\033[{r};1H\033[2K' for r in range(1, topo + 1))
                     + f'\033[{topo};1H')       # prompt volta para logo acima do painel
    sys.stdout.flush()


def painel_encerrar():
    """Devolve o terminal ao normal."""
    sys.stdout.write('\033[r\033[999;1H\n')
    sys.stdout.flush()


def linhas_painel(tb):
    """As 3 linhas de dados do painel; com RX mostra os medidores, so com TX mostra o que esta no ar."""
    if tb.tem_rx:
        return [f' BLER     : {tb.get_bler_probe()}',
                f' Vazao    : {tb.get_vazao_probe():,.0f} bit/s',
                f' Baudrate : {tb.get_baud_rate_probe():,.0f} baud']
    return [f' Modo     : {tb.modo}   (transmitindo)',
            f' Freq     : {tb.get_freq():,.0f} Hz',
            f' Ganho TX : RF {tb.get_rftxgain()} dB, IF {tb.get_iftxgain()} dB']


def monitorar(tb, periodo=0.5):
    """Atualiza, fixo no rodape, o que ja esta nos probes do flowgraph."""
    tb.flowgraph_started.wait()
    linhas = _linhas()
    while True:
        atual = _linhas()
        redim = ''
        if atual != linhas:                       # terminal redimensionado
            for r in range(linhas - ALTURA_PAINEL + 1, linhas + 1):
                redim += f'\033[{r};1H\033[2K'   # apaga o painel antigo
            linhas = atual
            redim += f'\033[1;{linhas - ALTURA_PAINEL}r'
        t = linhas - ALTURA_PAINEL + 1            # primeira linha do painel
        corpo = ''.join(f'\033[{t + 1 + i};1H\033[2K{txt}'
                        for i, txt in enumerate(linhas_painel(tb)))
        texto = ('\0337' + redim                                        # salva o cursor
                 + f'\033[{t};1H\033[2K{"-" * 60}'
                 + corpo
                 + '\0338')                                              # restaura o cursor
        sys.stdout.write(texto)
        sys.stdout.flush()
        time.sleep(periodo)


def main(top_block_cls=qpsk_5g_hw, options=None):
    global FATORES
    if options is None:
        options = ler_args()
    FATORES = fatores_do_modo(options.modo)
    tb = top_block_cls(modo=options.modo, tx_args=options.tx_args, rx_args=options.rx_args)

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    for nome in ('freq', 'rftxgain', 'iftxgain', 'rf_gain'):   # valores iniciais da linha de comando
        valor = getattr(options, nome)
        if valor is None:
            continue
        if nome in FATORES:
            getattr(tb, 'set_' + nome)(valor)
        else:
            print(f'aviso: --{nome} ignorado no modo {options.modo}')

    tb.start()
    tb.flowgraph_started.set()
    painel_iniciar()
    threading.Thread(target=monitorar, args=(tb,), daemon=True).start()

    print(f'Modo: {options.modo}')
    print('Radio: ' + ', '.join(f'{n} = {getattr(tb, "get_" + n)()}' for n in FATORES))
    print('Para mudar com o link rodando, digite por exemplo: ' + FATORES[-1] + ' <valor>')
    print('reset zera a BLER (so com RX)   |   clear limpa o terminal   |   setas cima/baixo: historico')
    try:
        while True:
            linha = input('Comando (ENTER vazio encerra): ').strip()
            if not linha:
                break
            aplicar_comando(tb, linha)
    except EOFError:
        pass
    finally:
        painel_encerrar()
    tb.stop()
    tb.wait()


if __name__ == '__main__':
    main()
