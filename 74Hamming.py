import numpy as np

EbNodB = 4

R = 4 / 7  # código de Hamming(7,4)

EbNo = 10 ** (EbNodB / 10)
SNR = 2 * R * EbNo
sigma = np.sqrt(1 / SNR)

k = 4  # numero de bits da mensagem
n = 7  # número de bits da palavras código

G = np.array(
    [
        [1, 0, 0, 0, 1, 0, 1],
        [0, 1, 0, 0, 1, 1, 1],
        [0, 0, 1, 0, 1, 1, 0],
        [0, 0, 0, 1, 0, 1, 1],
    ]
)

# gera números de 0 a 15
nums = np.arange(2**k, dtype=np.uint8)

# converte todos os números para numeros binarios(representação de 0000 até 1111)
all_bits = np.array([list(np.binary_repr(x, width=k)) for x in nums], dtype=int)

# Todas as palavras códigos possíveis
all_cwords = np.mod(all_bits @ G, 2)

n_blocks = 1000
n_erros_block = 0
n_erros_bit = 0

for i in range(n_blocks):
    msg = np.random.randint(0, 2, k)  # mensagem aleatória de k bits
    # Codificação
    c = msg
    c = np.append(c, (msg[0] + msg[1] + msg[2]) % 2)
    c = np.append(c, (msg[1] + msg[2] + msg[3]) % 2)
    c = np.append(c, (msg[0] + msg[1] + msg[3]) % 2)  # Hamming 7,4

    s = 1 - 2 * c  # conversão bit para simbolo bpsk
    r = s + sigma * np.random.randn(n)  # canal AWGN

    # Decodificação Hard
    b = r < 0  # limiar em zero
    dist = ((b + all_cwords) % 2) @ np.ones((7, 1))
    min_idx = np.argmin(dist)
    msg_cap1 = all_cwords[min_idx][:4]

    # Decodificação Soft
    corr = (1 - 2 * all_cwords) @ r.reshape(-1, 1)
    max_idx = np.argmax(corr)
    msg_cap2 = all_cwords[max_idx][:4]

    n_erros = np.sum(msg != msg_cap2)
    if n_erros > 0:
        n_erros_bit += n_erros
        n_erros_block += 1

BER_sim = n_erros_bit / k / n_blocks
BLER_sim = n_erros_block / n_blocks

print(f"Valor do Eb/No(dB): {EbNodB}")
print(f"Valor da  BLER simulada: {BLER_sim}")
print(f"Valor da  BER simulada: {BER_sim}")
print(f"Número de erros(blocos): {n_erros_block}")
print(f"Número de erros(bits): {n_erros_bit}")
print(f"Blocos simulados: {n_blocks}")