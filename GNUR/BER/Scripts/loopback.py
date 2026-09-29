#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: QPSK - 5G
# GNU Radio version: 3.10.12.0

from gnuradio import analog
from gnuradio import blocks
import pmt
from gnuradio import channels
from gnuradio.filter import firdes
from gnuradio import digital
from gnuradio import filter
from gnuradio import fec
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
import numpy as np
import threading


class epy_block_0_0_blk(gr.basic_block):
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


class qpsk_loopback_5g(gr.top_block):

    def __init__(self, noise_voltage=0.0, freq_offset=0.0, epsilon=1.0):
        gr.top_block.__init__(self, "QPSK - 5G", catch_exceptions=True)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables
        ##################################################
        self.bit_rate = bit_rate = 800e3
        self.sps = sps = 3
        self.baud_rate = baud_rate = bit_rate / 1
        self.samp_rate = samp_rate = baud_rate * sps
        self.RelSeq = RelSeq = np.array([0, 1, 2, 4, 8, 16, 32, 3, 5, 64, 9, 6, 17, 10, 18, 128, 12, 33, 65, 20, 256, 34, 24, 36, 7, 129, 66, 512, 11, 40, 68, 130, 19, 13, 48, 14, 72, 257, 21, 132, 35, 258, 26, 513, 80, 37, 25, 22, 136, 260, 264, 38, 514, 96, 67, 41, 144, 28, 69, 42, 516, 49, 74, 272, 160, 520, 288, 528, 192, 544, 70, 44, 131, 81, 50, 73, 15, 320, 133, 52, 23, 134, 384, 76, 137, 82, 56, 27, 97, 39, 259, 84, 138, 145, 261, 29, 43, 98, 515, 88, 140, 30, 146, 71, 262, 265, 161, 576, 45, 100, 640, 51, 148, 46, 75, 266, 273, 517, 104, 162, 53, 193, 152, 77, 164, 768, 268, 274, 518, 54, 83, 57, 521, 112, 135, 78, 289, 194, 85, 276, 522, 58, 168, 139, 99, 86, 60, 280, 89, 290, 529, 524, 196, 141, 101, 147, 176, 142, 530, 321, 31, 200, 90, 545, 292, 322, 532, 263, 149, 102, 105, 304, 296, 163, 92, 47, 267, 385, 546, 324, 208, 386, 150, 153, 165, 106, 55, 328, 536, 577, 548, 113, 154, 79, 269, 108, 578, 224, 166, 519, 552, 195, 270, 641, 523, 275, 580, 291, 59, 169, 560, 114, 277, 156, 87, 197, 116, 170, 61, 531, 525, 642, 281, 278, 526, 177, 293, 388, 91, 584, 769, 198, 172, 120, 201, 336, 62, 282, 143, 103, 178, 294, 93, 644, 202, 592, 323, 392, 297, 770, 107, 180, 151, 209, 284, 648, 94, 204, 298, 400, 608, 352, 325, 533, 155, 210, 305, 547, 300, 109, 184, 534, 537, 115, 167, 225, 326, 306, 772, 157, 656, 329, 110, 117, 212, 171, 776, 330, 226, 549, 538, 387, 308, 216, 416, 271, 279, 158, 337, 550, 672, 118, 332, 579, 540, 389, 173, 121, 553, 199, 784, 179, 228, 338, 312, 704, 390, 174, 554, 581, 393, 283, 122, 448, 353, 561, 203, 63, 340, 394, 527, 582, 556, 181, 295, 285, 232, 124, 205, 182, 643, 562, 286, 585, 299, 354, 211, 401, 185, 396, 344, 586, 645, 593, 535, 240, 206, 95, 327, 564, 800, 402, 356, 307, 301, 417, 213, 568, 832, 588, 186, 646, 404, 227, 896, 594, 418, 302, 649, 771, 360, 539, 111, 331, 214, 309, 188, 449, 217, 408, 609, 596, 551, 650, 229, 159, 420, 310, 541, 773, 610, 657, 333, 119, 600, 339, 218, 368, 652, 230, 391, 313, 450, 542, 334, 233, 555, 774, 175, 123, 658, 612, 341, 777, 220, 314, 424, 395, 673, 583, 355, 287, 183, 234, 125, 557, 660, 616, 342, 316, 241, 778, 563, 345, 452, 397, 403, 207, 674, 558, 785, 432, 357, 187, 236, 664, 624, 587, 780, 705, 126, 242, 565, 398, 346, 456, 358, 405, 303, 569, 244, 595, 189, 566, 676, 361, 706, 589, 215, 786, 647, 348, 419, 406, 464, 680, 801, 362, 590, 409, 570, 788, 597, 572, 219, 311, 708, 598, 601, 651, 421, 792, 802, 611, 602, 410, 231, 688, 653, 248, 369, 190, 364, 654, 659, 335, 480, 315, 221, 370, 613, 422, 425, 451, 614, 543, 235, 412, 343, 372, 775, 317, 222, 426, 453, 237, 559, 833, 804, 712, 834, 661, 808, 779, 617, 604, 433, 720, 816, 836, 347, 897, 243, 662, 454, 318, 675, 618, 898, 781, 376, 428, 665, 736, 567, 840, 625, 238, 359, 457, 399, 787, 591, 678, 434, 677, 349, 245, 458, 666, 620, 363, 127, 191, 782, 407, 436, 626, 571, 465, 681, 246, 707, 350, 599, 668, 790, 460, 249, 682, 573, 411, 803, 789, 709, 365, 440, 628, 689, 374, 423, 466, 793, 250, 371, 481, 574, 413, 603, 366, 468, 655, 900, 805, 615, 684, 710, 429, 794, 252, 373, 605, 848, 690, 713, 632, 482, 806, 427, 904, 414, 223, 663, 692, 835, 619, 472, 455, 796, 809, 714, 721, 837, 716, 864, 810, 606, 912, 722, 696, 377, 435, 817, 319, 621, 812, 484, 430, 838, 667, 488, 239, 378, 459, 622, 627, 437, 380, 818, 461, 496, 669, 679, 724, 841, 629, 351, 467, 438, 737, 251, 462, 442, 441, 469, 247, 683, 842, 738, 899, 670, 783, 849, 820, 728, 928, 791, 367, 901, 630, 685, 844, 633, 711, 253, 691, 824, 902, 686, 740, 850, 375, 444, 470, 483, 415, 485, 905, 795, 473, 634, 744, 852, 960, 865, 693, 797, 906, 715, 807, 474, 636, 694, 254, 717, 575, 913, 798, 811, 379, 697, 431, 607, 489, 866, 723, 486, 908, 718, 813, 476, 856, 839, 725, 698, 914, 752, 868, 819, 814, 439, 929, 490, 623, 671, 739, 916, 463, 843, 381, 497, 930, 821, 726, 961, 872, 492, 631, 729, 700, 443, 741, 845, 920, 382, 822, 851, 730, 498, 880, 742, 445, 471, 635, 932, 687, 903, 825, 500, 846, 745, 826, 732, 446, 962, 936, 475, 853, 867, 637, 907, 487, 695, 746, 828, 753, 854, 857, 504, 799, 255, 964, 909, 719, 477, 915, 638, 748, 944, 869, 491, 699, 754, 858, 478, 968, 383, 910, 815, 976, 870, 917, 727, 493, 873, 701, 931, 756, 860, 499, 731, 823, 922, 874, 918, 502, 933, 743, 760, 881, 494, 702, 921, 501, 876, 847, 992, 447, 733, 827, 934, 882, 937, 963, 747, 505, 855, 924, 734, 829, 965, 938, 884, 506, 749, 945, 966, 755, 859, 940, 830, 911, 871, 639, 888, 479, 946, 750, 969, 508, 861, 757, 970, 919, 875, 862, 758, 948, 977, 923, 972, 761, 877, 952, 495, 703, 935, 978, 883, 762, 503, 925, 878, 735, 993, 885, 939, 994, 980, 926, 764, 941, 967, 886, 831, 947, 507, 889, 984, 751, 942, 996, 971, 890, 509, 949, 973, 1000, 892, 950, 863, 759, 1008, 510, 979, 953, 763, 974, 954, 879, 981, 982, 927, 995, 765, 956, 887, 985, 997, 986, 943, 891, 998, 766, 511, 988, 1001, 951, 1002, 893, 975, 894, 1009, 955, 1004, 1010, 957, 983, 958, 987, 1012, 999, 1016, 767, 989, 1003, 990, 1005, 959, 1011, 1013, 895, 1006, 1014, 1017, 1018, 991, 1020, 1007, 1015, 1019, 1021, 1022, 1023])
        self.N = N = 1024
        self.K = K = 512
        self.rrc_filter_taps = rrc_filter_taps = firdes.root_raised_cosine(sps, samp_rate,baud_rate, 0.25, (11*sps))
        self.frozen_val = frozen_val = np.zeros(N-K, dtype=int)
        self.frozen_pos = frozen_pos = np.sort(RelSeq[RelSeq < N][:N-K])
        self.taps = taps = np.array(rrc_filter_taps)
        self.sync_len = sync_len = 32
        self.qpsk = qpsk = digital.constellation_qpsk().base()
        self.qpsk.set_npwr(1.0)
        self.payload_len = payload_len = 1
        self.noise_voltage = noise_voltage
        self.freq_offset = freq_offset
        self.epsilon = epsilon
        self.access_code = access_code = "11100001010110101110100010010011"
        self.PC_enc = PC_enc = fec.polar_encoder.make(N,K, frozen_pos, frozen_val, False)
        self.PC_dec = PC_dec = fec.polar_decoder_sc.make(N,K, frozen_pos, frozen_val)

        ##################################################
        # Blocks
        ##################################################

        self.interp_fir_filter_xxx_1 = filter.interp_fir_filter_ccc(1, taps/sps)
        self.interp_fir_filter_xxx_1.declare_sample_delay(0)
        self.interp_fir_filter_xxx_0 = filter.interp_fir_filter_ccc(sps, taps)
        self.interp_fir_filter_xxx_0.declare_sample_delay(0)
        self.fec_extended_tagged_encoder_0 = fec.extended_tagged_encoder(encoder_obj_list=PC_enc, puncpat='11', lentagname="quadro", mtu=1500)
        self.fec_extended_tagged_decoder_0 = self.fec_extended_tagged_decoder_0 = fec_extended_tagged_decoder_0 = fec.extended_tagged_decoder(decoder_obj_list=PC_dec, ann=None, puncpat='11', integration_period=10000, lentagname="quadro", mtu=1500)
        self.epy_block_0_0 = epy_block_0_0_blk(access_code=access_code, payload_len_in_bits=N, tag_key="quadro", threshold=0.6)
        self.digital_symbol_sync_xx_0 = digital.symbol_sync_cc(
            digital.TED_GARDNER,
            sps,
            0.045,
            1.0,
            1.0,
            0.01,
            1,
            digital.constellation_bpsk().base(),
            digital.IR_MMSE_8TAP,
            128,
            [])
        self.digital_costas_loop_cc_0 = digital.costas_loop_cc((2*np.pi/200), 4, False)
        self.digital_constellation_soft_decoder_cf_0 = digital.constellation_soft_decoder_cf(qpsk, -1)
        self.digital_constellation_encoder_bc_0 = digital.constellation_encoder_bc(qpsk)
        self.channels_channel_model_0 = channels.channel_model(
            noise_voltage=self.noise_voltage,
            frequency_offset=freq_offset,
            epsilon=epsilon,
            taps=[1.0],
            noise_seed=0,
            block_tags=True)
        self.blocks_vector_source_x_0 = blocks.vector_source_b((0xE1, 0x5A, 0xE8, 0x93), True, 1, [])
        self.blocks_unpack_k_bits_bb_0_0 = blocks.unpack_k_bits_bb(8)
        self.blocks_unpack_k_bits_bb_0 = blocks.unpack_k_bits_bb(8)
        self.blocks_throttle2_0 = blocks.throttle( gr.sizeof_char*1, bit_rate, True, 0 if "auto" == "auto" else max( int(float(0.1) * bit_rate) if "auto" == "time" else int(0.1), 1) )
        self.blocks_stream_to_tagged_stream_0 = blocks.stream_to_tagged_stream(gr.sizeof_char, 1, K, "quadro")
        self.blocks_stream_mux_0 = blocks.stream_mux(gr.sizeof_char*1, (32, N))
        self.blocks_pack_k_bits_bb_1 = blocks.pack_k_bits_bb(2)
        self.blocks_pack_k_bits_bb_0 = blocks.pack_k_bits_bb(8)
        self.blocks_multiply_const_vxx_0 = blocks.multiply_const_cc(0.5)
        self.blocks_file_source_0 = blocks.file_source(gr.sizeof_char*1, 'tx.txt', False, 0, 0)
        self.blocks_file_source_0.set_begin_tag(pmt.PMT_NIL)
        self.blocks_file_sink_0 = blocks.file_sink(gr.sizeof_char*1, 'tx_hat.txt', False)
        self.blocks_file_sink_0.set_unbuffered(True)
        self.analog_agc_xx_0 = analog.agc_cc((1e-4), 1.0, 1.0, 1e3)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_agc_xx_0, 0), (self.digital_symbol_sync_xx_0, 0))
        self.connect((self.blocks_file_source_0, 0), (self.blocks_unpack_k_bits_bb_0, 0))
        self.connect((self.blocks_multiply_const_vxx_0, 0), (self.digital_costas_loop_cc_0, 0))
        self.connect((self.blocks_pack_k_bits_bb_0, 0), (self.blocks_file_sink_0, 0))
        self.connect((self.blocks_pack_k_bits_bb_1, 0), (self.digital_constellation_encoder_bc_0, 0))
        self.connect((self.blocks_stream_mux_0, 0), (self.blocks_throttle2_0, 0))
        self.connect((self.blocks_stream_to_tagged_stream_0, 0), (self.fec_extended_tagged_encoder_0, 0))
        self.connect((self.blocks_throttle2_0, 0), (self.blocks_pack_k_bits_bb_1, 0))
        self.connect((self.blocks_unpack_k_bits_bb_0, 0), (self.blocks_stream_to_tagged_stream_0, 0))
        self.connect((self.blocks_unpack_k_bits_bb_0_0, 0), (self.blocks_stream_mux_0, 0))
        self.connect((self.blocks_vector_source_x_0, 0), (self.blocks_unpack_k_bits_bb_0_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.interp_fir_filter_xxx_1, 0))
        self.connect((self.digital_constellation_encoder_bc_0, 0), (self.interp_fir_filter_xxx_0, 0))
        self.connect((self.digital_constellation_soft_decoder_cf_0, 0), (self.epy_block_0_0, 0))
        self.connect((self.digital_costas_loop_cc_0, 0), (self.digital_constellation_soft_decoder_cf_0, 0))
        self.connect((self.digital_symbol_sync_xx_0, 0), (self.blocks_multiply_const_vxx_0, 0))
        self.connect((self.epy_block_0_0, 0), (self.fec_extended_tagged_decoder_0, 0))
        self.connect((self.fec_extended_tagged_decoder_0, 0), (self.blocks_pack_k_bits_bb_0, 0))
        self.connect((self.fec_extended_tagged_encoder_0, 0), (self.blocks_stream_mux_0, 1))
        self.connect((self.interp_fir_filter_xxx_0, 0), (self.channels_channel_model_0, 0))
        self.connect((self.interp_fir_filter_xxx_1, 0), (self.analog_agc_xx_0, 0))

    def get_bit_rate(self):
        return self.bit_rate

    def set_bit_rate(self, bit_rate):
        self.bit_rate = bit_rate
        self.set_baud_rate(self.bit_rate / 1)
        self.blocks_throttle2_0.set_sample_rate(self.bit_rate)

    def get_sps(self):
        return self.sps

    def set_sps(self, sps):
        self.sps = sps
        self.set_rrc_filter_taps(firdes.root_raised_cosine(self.sps, self.samp_rate, self.baud_rate, 0.25, (11*self.sps)))
        self.set_samp_rate(self.baud_rate * self.sps)
        self.digital_symbol_sync_xx_0.set_sps(self.sps)
        self.interp_fir_filter_xxx_1.set_taps(self.taps/self.sps)

    def get_baud_rate(self):
        return self.baud_rate

    def set_baud_rate(self, baud_rate):
        self.baud_rate = baud_rate
        self.set_rrc_filter_taps(firdes.root_raised_cosine(self.sps, self.samp_rate, self.baud_rate, 0.25, (11*self.sps)))
        self.set_samp_rate(self.baud_rate * self.sps)

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate
        self.set_rrc_filter_taps(firdes.root_raised_cosine(self.sps, self.samp_rate, self.baud_rate, 0.25, (11*self.sps)))

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
        self.blocks_stream_to_tagged_stream_0.set_packet_len(self.K)
        self.blocks_stream_to_tagged_stream_0.set_packet_len_pmt(self.K)

    def get_rrc_filter_taps(self):
        return self.rrc_filter_taps

    def set_rrc_filter_taps(self, rrc_filter_taps):
        self.rrc_filter_taps = rrc_filter_taps
        self.set_taps(np.array(self.rrc_filter_taps))

    def get_frozen_val(self):
        return self.frozen_val

    def set_frozen_val(self, frozen_val):
        self.frozen_val = frozen_val

    def get_frozen_pos(self):
        return self.frozen_pos

    def set_frozen_pos(self, frozen_pos):
        self.frozen_pos = frozen_pos

    def get_taps(self):
        return self.taps

    def set_taps(self, taps):
        self.taps = taps
        self.interp_fir_filter_xxx_0.set_taps(self.taps)
        self.interp_fir_filter_xxx_1.set_taps(self.taps/self.sps)

    def get_sync_len(self):
        return self.sync_len

    def set_sync_len(self, sync_len):
        self.sync_len = sync_len

    def get_qpsk(self):
        return self.qpsk

    def set_qpsk(self, qpsk):
        self.qpsk = qpsk
        self.digital_constellation_encoder_bc_0.set_constellation(self.qpsk)
        self.digital_constellation_soft_decoder_cf_0.set_constellation(self.qpsk)

    def get_payload_len(self):
        return self.payload_len

    def set_payload_len(self, payload_len):
        self.payload_len = payload_len

    def get_noise_voltage(self):
        return self.noise_voltage

    def set_noise_voltage(self, noise_voltage):
        self.noise_voltage = noise_voltage
        self.channels_channel_model_0.set_noise_voltage(self.noise_voltage)

    def get_freq_offset(self):
        return self.freq_offset

    def set_freq_offset(self, freq_offset):
        self.freq_offset = freq_offset
        self.channels_channel_model_0.set_frequency_offset(self.freq_offset)

    def get_epsilon(self):
        return self.epsilon

    def set_epsilon(self, epsilon):
        self.epsilon = epsilon
        self.channels_channel_model_0.set_timing_offset(self.epsilon)

    def get_access_code(self):
        return self.access_code

    def set_access_code(self, access_code):
        self.access_code = access_code

    def get_PC_enc(self):
        return self.PC_enc

    def set_PC_enc(self, PC_enc):
        self.PC_enc = PC_enc

    def get_PC_dec(self):
        return self.PC_dec

    def set_PC_dec(self, PC_dec):
        self.PC_dec = PC_dec


def argument_parser():
    parser = ArgumentParser()
    parser.add_argument(
        "-n", "--noise-voltage", dest="noise_voltage", type=eng_float, default=eng_notation.num_to_str(0.0),
        help="Ruído do canal (desvio padrão do AWGN) [default=%(default)r]")
    parser.add_argument(
        "-f", "--freq-offset", dest="freq_offset", type=eng_float, default=eng_notation.num_to_str(0.0),
        help="Offset de frequência do canal, em Hz [default=%(default)r]")
    parser.add_argument(
        "-e", "--epsilon", dest="epsilon", type=eng_float, default=eng_notation.num_to_str(1.0),
        help="Offset de timing (clock skew) do canal [default=%(default)r]")
    return parser


def main(top_block_cls=qpsk_loopback_5g, options=None):
    if options is None:
        options = argument_parser().parse_args()

    tb = top_block_cls(
        noise_voltage=options.noise_voltage,
        freq_offset=options.freq_offset,
        epsilon=options.epsilon,
    )

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    tb.start()
    tb.flowgraph_started.set()

    tb.wait()


if __name__ == '__main__':
    main()