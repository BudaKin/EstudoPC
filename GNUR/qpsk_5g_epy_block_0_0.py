import numpy as np
import pmt
from gnuradio import gr

class blk(gr.basic_block):
    def __init__(self, access_code="", payload_len_in_bits=0, tag_key=""):
        gr.basic_block.__init__(
            self,
            name='Find Access Code',
            in_sig=[np.uint8],
            out_sig=[np.uint8],
        )
        self.access_code = np.array([int(b) for b in access_code])
        self.payload_len_in_bits = payload_len_in_bits
        self.tag_key = pmt.intern(tag_key)

    def general_work(self, input_items, output_items):
        in0 = input_items[0]
        out0 = output_items[0]
        ac_len = self.access_code.size
        pl_len = self.payload_len_in_bits
        if len(in0) < ac_len + pl_len:
            return 0
        if len(out0) < pl_len:
            return 0
        if not np.array_equal(in0[:ac_len], self.access_code):
            self.consume(0, 1)  # Não é o mais otimizado. :D
            return 0
        self.consume(0, ac_len + pl_len)
        out0[:pl_len] = in0[ac_len: ac_len + pl_len]
        self.add_item_tag(0, self.nitems_written(0), self.tag_key, pmt.from_long(pl_len))
        return pl_len
