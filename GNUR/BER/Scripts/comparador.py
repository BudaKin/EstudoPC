import sys
import numpy as np

tx = np.fromfile(sys.argv[1] if len(sys.argv) > 1 else "tx.txt", np.uint8)
rx = np.fromfile(sys.argv[2] if len(sys.argv) > 2 else "tx_hat.txt", np.uint8)

# tamanhos reais, lidos a partir do tx.txt original (sem ruído)
H = int(np.argmax(tx != ord("I")))          # tamanho do preâmbulo
T = int(np.argmax(tx[::-1] != ord("F")))    # tamanho do pós-âmbulo
P = len(tx) - H - T                         # tamanho do payload
ref = np.unpackbits(tx[H:H + P])


def borda(d, c):
    """Tamanho real do bloco de 'c' no começo de d (tolera erros de bit)."""
    s = np.cumsum(np.where(d == ord(c), 1, -1))
    return int(np.argmax(np.concatenate(([0], s))))


ini = borda(rx, "I")                    # onde o payload começa no recebido
fim = len(rx) - borda(rx[::-1], "F")    # onde o payload termina no recebido


def ber(i):
    b = np.unpackbits(rx[i:i + P])
    n = min(len(ref), len(b))
    return int((ref[:n] != b[:n]).sum()), n


# testa alguns bytes ao redor da borda (payload pode começar/terminar com I/F por acaso)
err, n = min((ber(i) for i in range(max(0, ini - 8), ini + 9)), key=lambda t: t[0] / max(t[1], 1))

pay_len = fim - ini
print(f"Preâmbulo:  {ini} B (esperado {H})")
print(f"Payload:    {pay_len} B (esperado {P})")
print(f"Pós-âmbulo: {len(rx) - fim} B (esperado {T})")
ber_val = err / max(n, 1)
print(f"BER payload: {ber_val*100:.2e}%  ({err} bits errados / {n})")
if abs(pay_len - P) > 0.05 * P:
    print(f"ATENÇÃO: payload {pay_len - P:+d} B, fora da tolerância de 5%; "
          "borda do preâmbulo/pós-âmbulo pode ter sido mal detectada.")
elif ber_val > 0.05:
    print("ATENÇÃO: BER muito alto, provável perda/inserção de bytes no payload (desalinhado).")