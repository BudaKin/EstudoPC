#!/usr/bin/env python3
"""Comparador de BER para arquivos gerados pelo gerar_tx.py.

Uso:
    python comparador.py [tx.txt] [tx_hat.txt]

Como funciona:
  1. Lê o layout (lead-in, nº de blocos, tamanho do bloco) do próprio tx.txt.
  2. Procura o SYNC de cada bloco no recebido, tolerando alguns bits errados.
  3. Usa como "âncora" os blocos cujo número de sequência passa no CRC32
     (3 cópias por bloco). Entre duas âncoras, o espaçamento em bytes diz se
     está tudo presente, se faltam blocos inteiros ou se houve slip de bytes.
  4. Compara só os bytes de dados de blocos bem alinhados. Blocos perdidos ou
     com perda/inserção de bytes no meio são contados à parte e NÃO entram
     na BER (senão eles jogariam a BER para ~50%).

Premissa: o recebido é uma sequência de bytes (perdas/inserções em bytes).
"""
import sys
from zlib import crc32
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# Deve ser igual ao gerar_tx.py
SYNC = np.frombuffer(bytes.fromhex("1ACFFC1D5E3A97B4"), np.uint8)
L_SYNC, L_SEQ = len(SYNC), 24
H = L_SYNC + L_SEQ
LIMIAR = 10  # bits diferentes tolerados no SYNC (de 64)

POP = np.array([bin(i).count("1") for i in range(256)], np.uint8)


def achar_sync(x, limiar):
    """Posições (em bytes) onde o SYNC aparece com <= limiar bits errados."""
    pos, dist, passo = [], [], 1 << 20
    for a in range(0, max(len(x) - L_SYNC + 1, 0), passo):
        w = sliding_window_view(x[a:a + passo + L_SYNC - 1], L_SYNC)
        d = POP[w ^ SYNC].sum(axis=1, dtype=np.int32)
        i = np.flatnonzero(d <= limiar)
        pos.append(a + i)
        dist.append(d[i])
    if not pos:
        return []
    cand = zip(np.concatenate(pos).tolist(), np.concatenate(dist).tolist())
    # candidatos colados: fica o de menor distância
    out = []
    for p, d in cand:
        if out and p - out[-1][0] < L_SYNC + L_SEQ:
            if d < out[-1][1]:
                out[-1] = (p, d)
        else:
            out.append((p, d))
    return [p for p, _ in out]


def ler_seq(x, p):
    """Número de sequência da primeira cópia com CRC32 válido (ou None)."""
    for i in range(3):
        s = x[p + L_SYNC + 8 * i:p + L_SYNC + 8 * i + 8].tobytes()
        if len(s) == 8 and crc32(s[:4]) == int.from_bytes(s[4:], "big"):
            return int.from_bytes(s[:4], "big")
    return None


def wilson(k, n, z=1.96):
    """Intervalo de confiança de 95% para k erros em n bits."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def main():
    tx = np.fromfile(sys.argv[1] if len(sys.argv) > 1 else "tx.txt", np.uint8)
    rx = np.fromfile(sys.argv[2] if len(sys.argv) > 2 else "tx_hat.txt", np.uint8)

    # --- layout lido do arquivo transmitido (sem ruído) ---
    w = sliding_window_view(tx, L_SYNC)
    ptx = np.flatnonzero((w == SYNC).all(axis=1))
    if len(ptx) < 2 or len(set(np.diff(ptx).tolist())) != 1:
        sys.exit("tx.txt não tem o formato do gerar_tx.py (SYNC não encontrado).")
    N, Lb, lead = len(ptx), int(ptx[1] - ptx[0]), int(ptx[0])
    B = Lb - H
    ref = tx[lead:lead + N * Lb].reshape(N, Lb)[:, H:]

    # --- âncoras: SYNC achado + número de sequência válido e crescente ---
    achados, prev_k = [], -1
    for p in achar_sync(rx, LIMIAR):
        s = ler_seq(rx, p)
        if s is not None and prev_k < s < N:
            achados.append((p, s))
            prev_k = s

    if not achados:
        sys.exit("Nenhum bloco encontrado no recebido (ruído alto demais ou sem sinal).")

    def erros(p, k):
        got = rx[p + H:p + H + B]
        return int(POP[ref[k] ^ got].sum()) if len(got) == B else None

    medidos, deslizados = {}, set()
    for (p, k), (p2, k2) in zip(achados, achados[1:]):
        sp, d = p2 - p, k2 - k
        if sp == d * Lb:                      # tudo presente (SYNC/seq do meio podem ter falhado)
            for j in range(d):
                medidos[k + j] = erros(p + j * Lb, k + j)
        elif sp % Lb == 0 and 0 < sp // Lb < d:   # blocos inteiros perdidos depois de k
            medidos[k] = erros(p, k)
        else:                                 # perda/inserção de bytes dentro do bloco k
            deslizados.add(k)
    p, k = achados[-1]
    if p + Lb <= len(rx):
        medidos[k] = erros(p, k)

    medidos = {k: e for k, e in medidos.items() if e is not None}
    nb = len(medidos)
    err = sum(medidos.values())
    bits = nb * B * 8
    ber = err / bits if bits else float("nan")
    lo, hi = wilson(err, bits)
    com_erro = sum(1 for e in medidos.values() if e)

    print(f"Layout:      lead-in {lead} B | {N} blocos de {B} B | tail {len(tx) - lead - N * Lb} B")
    print(f"Recebido:    {len(rx)} B (enviado {len(tx)} B)")
    print(f"Blocos:      {nb} medidos | {len(deslizados)} com slip de bytes | "
          f"{N - nb - len(deslizados)} perdidos/indeterminados  (de {N})")
    print(f"BER payload: {ber:.3e}  ({err} bits errados / {bits})")
    print(f"IC 95%:      [{lo:.2e}, {hi:.2e}]")
    print(f"Blocos com erro: {com_erro}/{nb}"
          + (f"  | média {err / com_erro:.1f} erros por bloco com erro" if com_erro else ""))

    if err < 100:
        print(f"ATENÇÃO: só {err} erros observados, estimativa incerta. "
              f"Para ~100 erros seriam necessários ~{100 / max(ber, 1e-12):.2e} bits.")
    if nb < 0.9 * N:
        print("ATENÇÃO: mais de 10% dos blocos não foram medidos; "
              "verifique perda de quadros antes de confiar na BER.")

    # BER por décimo do arquivo (mostra se o ruído/transiente muda ao longo do tempo)
    if nb >= 20:
        ks = np.array(sorted(medidos))
        es = np.array([medidos[k] for k in ks])
        print("BER por décimo do arquivo:",
              " ".join(f"{s.sum() / (len(s) * B * 8):.1e}" for s in np.array_split(es, 10)))


if __name__ == "__main__":
    main()
