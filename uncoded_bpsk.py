import komm
import numpy as np

EbNodB = 10.5

R = 1 # uncoded = 1 bit per symbol

EbNo = 10**(EbNodB/10)
SNR = 2*R*EbNo
sigma = np.sqrt(1/SNR)
BER_teo = komm.gaussian_q(np.sqrt(2*EbNo))

N = 1000 # Número de bits da mensagem por bloco
n_blocks = 100000
n_erros = 0

for i in range(n_blocks):
    msg = np.random.randint(0, 2, N) # mensagem aleatória
    # Codificação -> seria nesta situação
    s = 1-2*msg # conversão bit para simbolo bpsk
    r = s + sigma*np.random.randn(N) # canal AWGN
    # Decodificação -> seria nesta situação
    msg_cap = (r < 0)  # limiar em zero

    n_erros += np.sum(msg != msg_cap)

BER_sim = n_erros/N/n_blocks

print(f"Valor do Eb/No(dB): {EbNodB}")
print(f"Bits simulados: {N*n_blocks}")
print(f"Valor da  BER teórica: {BER_teo}")
print(f"Valor da  BER simulada: {BER_sim}")