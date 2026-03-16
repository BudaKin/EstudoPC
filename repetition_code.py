import komm
import numpy as np

EbNodB = 6

R = 1/3 # código de repetição n=3

EbNo = 10**(EbNodB/10)
SNR = 2*R*EbNo
sigma = np.sqrt(1/SNR)

k = 1 # numero de bits da mensagem
n = 3 # número de bits da palavras código

n_blocks = 100000
n_erros = 0

for i in range(n_blocks):
    msg = np.random.randint(0, 2, k) # mensagem aleatória de k bits
    # Codificação
    c = np.repeat(msg, 3) # Repetição n=3
    s = 1-2*c # conversão bit para simbolo bpsk
    r = s + sigma*np.random.randn(n) # canal AWGN
    # Decodificação Hard

    b = (r < 0)  # limiar em zero
    if np.sum(b) > 1:
        msg_cap1 = 1
    else:
        msg_cap1 = 0

    # Decodificação Soft
    if sum(r) < 0:
        msg_cap2 = 1
    else:
        msg_cap2 = 0

    n_erros += np.sum(msg != msg_cap2)

BER_sim = n_erros/k/n_blocks

print(f"Valor do Eb/No(dB): {EbNodB}")
print(f"Bits simulados: {k*n_blocks}")
print(f"Número de erros: {n_erros}")
print(f"Valor da  BER simulada: {BER_sim}")