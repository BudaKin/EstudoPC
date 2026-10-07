#!/usr/bin/env python3
"""Gera o tx.txt para medir BER.

Estrutura do arquivo (tudo em bytes):

    [ LEAD-IN ][ BLOCO 0 ][ BLOCO 1 ] ... [ BLOCO N-1 ][ TAIL ]

    LEAD-IN / TAIL : bytes aleatórios descartáveis (o receptor "esquenta" e
                     esvazia os loops neles; nada disso entra na BER)
    BLOCO          : SYNC (8 B) | 3 x [SEQ (4 B) + CRC32 (4 B)] | DADOS (B bytes)

O SYNC permite ao comparador se realinhar em cada bloco, e o número de
sequência (3 cópias, cada uma com CRC32) diz qual bloco é. Assim, perder
um quadro custa só aquele bloco, e não o arquivo inteiro.

Uso:
    python gerar_tx.py                      # 2 MiB de payload -> tx.txt
    python gerar_tx.py --payload 5000000    # ~5 MB de payload
    python gerar_tx.py --bloco 512 --lead 8192 --saida tx.txt
"""
import argparse
from zlib import crc32
import numpy as np

# Palavra de sincronismo (64 bits). Deve ser a mesma no comparador.py.
SYNC = bytes.fromhex("1ACFFC1D5E3A97B4")
L_SYNC, L_SEQ = len(SYNC), 24
H = L_SYNC + L_SEQ  # tamanho do cabeçalho de cada bloco


def gerar(payload, bloco, lead, tail, seed):
    rng = np.random.default_rng(seed)
    n = -(-payload // bloco)  # nº de blocos (arredonda para cima)

    blocos = np.empty((n, H + bloco), np.uint8)
    blocos[:, :L_SYNC] = np.frombuffer(SYNC, np.uint8)
    seq = np.arange(n, dtype=">u4").view(np.uint8).reshape(n, 4)
    crc = np.array([crc32(s.tobytes()) for s in seq], dtype=">u4").view(np.uint8).reshape(n, 4)
    for i in range(3):
        a = L_SYNC + 8 * i
        blocos[:, a:a + 4] = seq
        blocos[:, a + 4:a + 8] = crc
    blocos[:, H:] = rng.integers(0, 256, (n, bloco), dtype=np.uint8)

    return np.concatenate([
        rng.integers(0, 256, lead, dtype=np.uint8),
        blocos.ravel(),
        rng.integers(0, 256, tail, dtype=np.uint8),
    ]), n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--payload", type=int, default=2 * 1024 * 1024,
                    help="bytes de dados úteis (default 2 MiB)")
    ap.add_argument("--bloco", type=int, default=1024,
                    help="bytes de dados por bloco (default 1024)")
    ap.add_argument("--lead", type=int, default=16384,
                    help="bytes descartáveis no início (default 16384)")
    ap.add_argument("--tail", type=int, default=4096,
                    help="bytes descartáveis no fim (default 4096)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--saida", default="tx.txt")
    a = ap.parse_args()

    dados, n = gerar(a.payload, a.bloco, a.lead, a.tail, a.seed)
    dados.tofile(a.saida)

    bits = n * a.bloco * 8
    print(f"{a.saida}: {len(dados)} B  (lead {a.lead} + {n} blocos x {H + a.bloco} + tail {a.tail})")
    print(f"Payload medido: {n * a.bloco} B = {bits:.3e} bits")
    print(f"BER mínima com ~100 erros: {100 / bits:.1e}   |   com 0 erros, BER < {3 / bits:.1e} (95%)")
