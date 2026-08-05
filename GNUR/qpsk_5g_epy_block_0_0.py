import numpy as np
import pmt
from gnuradio import gr

class blk(gr.basic_block):
    def __init__(self, access_code="", payload_len_in_bits=0, tag_key="", max_errors=0, threshold=0.5):
        gr.basic_block.__init__(
            self,
            name='Find Access Code',
            in_sig=[np.float32],
            out_sig=[np.float32],
        )
        self.access_code = np.array([int(b) for b in access_code])
        self.payload_len_in_bits = payload_len_in_bits
        self.tag_key = pmt.intern(tag_key)
        self.max_errors = max_errors
        self.threshold = threshold

    def general_work(self, input_items, output_items):
        in0 = input_items[0]
        out0 = output_items[0]
        ac_len = self.access_code.size
        pl_len = self.payload_len_in_bits
        if len(in0) < ac_len + pl_len:
            return 0
        if len(out0) < pl_len:
            return 0
        n_errors = np.count_nonzero((in0[:ac_len] >= self.threshold).astype(int) != self.access_code)
        if n_errors > self.max_errors:
            self.consume(0, 1)  # Não é o mais otimizado. :D
            return 0
        self.consume(0, ac_len + pl_len)
        out0[:pl_len] = in0[ac_len: ac_len + pl_len]
        self.add_item_tag(0, self.nitems_written(0), self.tag_key, pmt.from_long(pl_len))
        return pl_len


