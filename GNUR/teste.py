#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#
# SPDX-License-Identifier: GPL-3.0
#
# GNU Radio Python Flow Graph
# Title: Teste2
# GNU Radio version: 3.10.12.0

from PyQt5 import Qt
from gnuradio import qtgui
from gnuradio import blocks
import numpy
from gnuradio import channels
from gnuradio.filter import firdes
from gnuradio import digital
from gnuradio import fec
from gnuradio import gr
from gnuradio.fft import window
import sys
import signal
from PyQt5 import Qt
from argparse import ArgumentParser
from gnuradio.eng_arg import eng_float, intx
from gnuradio import eng_notation
import numpy as np
import threading



class teste(gr.top_block, Qt.QWidget):

    def __init__(self):
        gr.top_block.__init__(self, "Teste2", catch_exceptions=True)
        Qt.QWidget.__init__(self)
        self.setWindowTitle("Teste2")
        qtgui.util.check_set_qss()
        try:
            self.setWindowIcon(Qt.QIcon.fromTheme('gnuradio-grc'))
        except BaseException as exc:
            print(f"Qt GUI: Could not set Icon: {str(exc)}", file=sys.stderr)
        self.top_scroll_layout = Qt.QVBoxLayout()
        self.setLayout(self.top_scroll_layout)
        self.top_scroll = Qt.QScrollArea()
        self.top_scroll.setFrameStyle(Qt.QFrame.NoFrame)
        self.top_scroll_layout.addWidget(self.top_scroll)
        self.top_scroll.setWidgetResizable(True)
        self.top_widget = Qt.QWidget()
        self.top_scroll.setWidget(self.top_widget)
        self.top_layout = Qt.QVBoxLayout(self.top_widget)
        self.top_grid_layout = Qt.QGridLayout()
        self.top_layout.addLayout(self.top_grid_layout)

        self.settings = Qt.QSettings("gnuradio/flowgraphs", "teste")

        try:
            geometry = self.settings.value("geometry")
            if geometry:
                self.restoreGeometry(geometry)
        except BaseException as exc:
            print(f"Qt GUI: Could not restore geometry: {str(exc)}", file=sys.stderr)
        self.flowgraph_started = threading.Event()

        ##################################################
        # Variables
        ##################################################
        self.sps = sps = 50
        self.samp_rate = samp_rate = 32000
        self.freq = freq = 93.7e6
        self.bpsk = bpsk = digital.constellation_calcdist([-1,1], [0, 1],
        4, 1, digital.constellation.AMPLITUDE_NORMALIZATION).base()
        self.bpsk.set_npwr(1.0)
        self.PC_enc = PC_enc = fec.polar_encoder.make(16,8, [0,1,2,3,4,5,8,9], [0,0,0,0,0,0,0,0], False)
        self.PC_dec = PC_dec = fec.polar_decoder_sc.make(16,8, [0,1,2,3,4,5,8,9], [0,0,0,0,0,0,0,0])

        ##################################################
        # Blocks
        ##################################################

        self.fec_extended_tagged_encoder_0 = fec.extended_tagged_encoder(encoder_obj_list=PC_enc, puncpat='11', lentagname='packet_len', mtu=1500)
        self.fec_extended_tagged_decoder_0 = self.fec_extended_tagged_decoder_0 = fec_extended_tagged_decoder_0 = fec.extended_tagged_decoder(decoder_obj_list=PC_dec, ann=None, puncpat='11', integration_period=10000, lentagname='packet_len', mtu=1500)
        self.digital_constellation_soft_decoder_cf_0 = digital.constellation_soft_decoder_cf(bpsk, -1)
        self.digital_constellation_encoder_bc_0 = digital.constellation_encoder_bc(bpsk)
        self.channels_channel_model_0 = channels.channel_model(
            noise_voltage=(100e-3),
            frequency_offset=(100e-6),
            epsilon=1.0,
            taps=[1.0],
            noise_seed=0,
            block_tags=False)
        self.blocks_stream_to_tagged_stream_0 = blocks.stream_to_tagged_stream(gr.sizeof_char, 1, 8, "packet_len")
        self.blocks_file_sink_0_0 = blocks.file_sink(gr.sizeof_char*1, 'entrada', False)
        self.blocks_file_sink_0_0.set_unbuffered(False)
        self.blocks_file_sink_0 = blocks.file_sink(gr.sizeof_char*1, 'saida', False)
        self.blocks_file_sink_0.set_unbuffered(False)
        self.analog_random_source_x_0 = blocks.vector_source_b(list(map(int, numpy.random.randint(0, 2, 100000))), True)


        ##################################################
        # Connections
        ##################################################
        self.connect((self.analog_random_source_x_0, 0), (self.blocks_file_sink_0_0, 0))
        self.connect((self.analog_random_source_x_0, 0), (self.blocks_stream_to_tagged_stream_0, 0))
        self.connect((self.blocks_stream_to_tagged_stream_0, 0), (self.fec_extended_tagged_encoder_0, 0))
        self.connect((self.channels_channel_model_0, 0), (self.digital_constellation_soft_decoder_cf_0, 0))
        self.connect((self.digital_constellation_encoder_bc_0, 0), (self.channels_channel_model_0, 0))
        self.connect((self.digital_constellation_soft_decoder_cf_0, 0), (self.fec_extended_tagged_decoder_0, 0))
        self.connect((self.fec_extended_tagged_decoder_0, 0), (self.blocks_file_sink_0, 0))
        self.connect((self.fec_extended_tagged_encoder_0, 0), (self.digital_constellation_encoder_bc_0, 0))


    def closeEvent(self, event):
        self.settings = Qt.QSettings("gnuradio/flowgraphs", "teste")
        self.settings.setValue("geometry", self.saveGeometry())
        self.stop()
        self.wait()

        event.accept()

    def get_sps(self):
        return self.sps

    def set_sps(self, sps):
        self.sps = sps

    def get_samp_rate(self):
        return self.samp_rate

    def set_samp_rate(self, samp_rate):
        self.samp_rate = samp_rate

    def get_freq(self):
        return self.freq

    def set_freq(self, freq):
        self.freq = freq

    def get_bpsk(self):
        return self.bpsk

    def set_bpsk(self, bpsk):
        self.bpsk = bpsk
        self.digital_constellation_encoder_bc_0.set_constellation(self.bpsk)
        self.digital_constellation_soft_decoder_cf_0.set_constellation(self.bpsk)

    def get_PC_enc(self):
        return self.PC_enc

    def set_PC_enc(self, PC_enc):
        self.PC_enc = PC_enc

    def get_PC_dec(self):
        return self.PC_dec

    def set_PC_dec(self, PC_dec):
        self.PC_dec = PC_dec




def main(top_block_cls=teste, options=None):

    qapp = Qt.QApplication(sys.argv)

    tb = top_block_cls()

    tb.start()
    tb.flowgraph_started.set()

    tb.show()

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()

        Qt.QApplication.quit()

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    timer = Qt.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    qapp.exec_()

if __name__ == '__main__':
    main()
