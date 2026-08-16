import numpy as np
import pmt
from gnuradio import gr


class blk(gr.basic_block):
    """
    Encontra o access code em um stream de LLRs (float) e extrai o payload.

    A versao original testava uma unica posicao por chamada e, quando nao
    batia, avancava 1 amostra (self.consume(0, 1)) e retornava. Sob ruido
    alto o access code quase nunca eh encontrado, entao o bloco passa a ser
    chamado uma vez POR AMOSTRA. Cada chamada tem overhead de scheduler do
    GNU Radio (troca de contexto Python <-> C++, locks de buffer, etc.), e a
    taxa de chamadas necessarias explode para a taxa de amostragem (dezenas
    de MSa/s aqui). O bloco nao trava tecnicamente, mas fica tao atras do
    tempo real que a saida parece nunca mais voltar.

    Esta versao varre TODO o buffer de entrada disponivel de uma vez, com
    numpy (janela deslizante + correlacao vetorizada), reduzindo o numero de
    chamadas de general_work de O(N amostras) para O(N / tamanho_do_buffer).
    """

    def __init__(self, access_code="", payload_len_in_bits=0,
                 tag_key="", threshold=0.85):
        gr.basic_block.__init__(
            self,
            name='Find Access Code',
            in_sig=[np.float32],
            out_sig=[np.float32],
        )
        self.access_code = np.array(
            [2 * int(b) - 1 for b in access_code], dtype=np.float32
        )
        self.ac_norm = np.linalg.norm(self.access_code)
        self.payload_len_in_bits = payload_len_in_bits
        self.tag_key = pmt.intern(tag_key)
        self.threshold = threshold

    def general_work(self, input_items, output_items):
        in0 = input_items[0]
        out0 = output_items[0]
        ac_len = self.access_code.size
        pl_len = self.payload_len_in_bits
        frame_len = ac_len + pl_len

        n_avail = len(in0)
        if n_avail < frame_len:
            return 0
        if len(out0) < pl_len:
            return 0

        # Quantas posicoes iniciais cabem inteiramente no buffer disponivel
        n_positions = n_avail - frame_len + 1

        # Janela deslizante vetorizada: shape (n_positions, ac_len)
        windows = np.lib.stride_tricks.sliding_window_view(
            in0[:n_avail - pl_len], ac_len
        )[:n_positions]

        # Correlacao normalizada (cosseno), mais robusta a ruido do que
        # normalizar so pela energia L1 (soma dos modulos)
        norms = np.linalg.norm(windows, axis=1)
        with np.errstate(invalid='ignore', divide='ignore'):
            corr = (windows @ self.access_code) / (norms * self.ac_norm)
        corr = np.nan_to_num(corr, nan=0.0)

        matches = np.flatnonzero(corr >= self.threshold)

        if matches.size == 0:
            # Nenhum candidato nesse pedaco do buffer: descarta tudo que ja
            # foi verificado de uma vez so (em vez de amostra por amostra)
            self.consume(0, n_positions)
            return 0

        idx = int(matches[0])
        if idx > 0:
            # Achou, mas nao no inicio do buffer: descarta o lixo antes do
            # achado numa unica chamada e deixa o casamento para a proxima
            self.consume(0, idx)
            return 0

        # idx == 0: access code casou exatamente no inicio do buffer
        self.consume(0, frame_len)
        out0[:pl_len] = in0[ac_len:frame_len]
        self.add_item_tag(
            0, self.nitems_written(0), self.tag_key, pmt.from_long(pl_len)
        )
        return pl_len
