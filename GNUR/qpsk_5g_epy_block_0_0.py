import numpy as np
import pmt
from gnuradio import gr


class blk(gr.basic_block):
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
