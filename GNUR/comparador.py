
from itertools import zip_longest

ARQUIVO1 = "entrada"
ARQUIVO2 = "saida"

with open(ARQUIVO1, "rb") as f:
    dados1 = f.read()

with open(ARQUIVO2, "rb") as f:
    dados2 = f.read()

bits_iguais = 0
bits_total = max(len(dados1), len(dados2)) * 8

for a, b in zip_longest(dados1, dados2, fillvalue=0):
    xor = a ^ b
    bits_diferentes = bin(xor).count("1")
    bits_iguais += 8 - bits_diferentes

porcentagem = (bits_iguais / bits_total) * 100

print(f"{porcentagem:.6f}% iguais")