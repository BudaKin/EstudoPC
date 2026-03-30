import numpy as np
import matplotlib.pyplot as plt

# Esse código é meramente um teste que o professor mostdrou em aula, fiz questão de tentar mostrar de forma diferente
# as informações, mas mantive a base utilizada

# definição da função
def f(x):
    return np.abs(np.log(np.tanh(np.abs(x) / 2)))

# vetor x
x = np.arange(-5, 5.01, 0.01)

# plot
# plt.plot(x, f(x))
# plt.xlabel("x")
# plt.ylabel("f(x)")
# plt.title("f(x) = abs(log(tanh(abs(x)/2)))")
# plt.grid(True)
# plt.show()

# 1° trecho (spc (3,2))

sigma = 0.8

r = 1 + sigma * np.random.randn(3)
l = (2/sigma**2) * r

lext1 = f(f(l[1])+f(l[2])) # f(f(l2) + f(l3))
lext2 = f(f(l[0])+f(l[2])) # f(f(l1) + f(l3))
lext3 = f(f(l[0])+f(l[1])) # f(f(l1) + f(l2))

lext1_a = min(l[1], l[2]) * np.sign(l[1]) * np.sign(l[2])
lext2_a = min(l[0], l[2]) * np.sign(l[0]) * np.sign(l[2])
lext3_a = min(l[0], l[1]) * np.sign(l[0]) * np.sign(l[1])

# comparacão do método de utilizar a função f e a aproximação de min
print("============================(3,2)============================")
print(f"l_ext1 = {lext1}, l_ext2 = {lext1_a}, diff = {lext1-lext1_a}")
print(f"l_ext2 = {lext2}, l_ext2 = {lext2_a}, diff = {lext2-lext2_a}")
print(f"l_ext3 = {lext3}, l_ext2 = {lext3_a}, diff = {lext3-lext3_a}")
print("=============================================================")

# 2° trecho (spc(n, n-1))

n = 6

r = 1 + sigma * np.random.randn(n)
l = (2/sigma**2) * r

# vetor exemplo utilizado pelo video
l = np.array([3.93797634864049,1.23767920207574,6.55074635023807,-1.15379104713424,2.86939388478627,2.5213823959816])

print(f"l = {l}")

S = np.prod(np.sign(l))

m1 = min(np.abs(l))
pos = np.argmin(np.abs(l))
m2 = np.min(np.delete(np.abs(l), pos))

print(f"Mínimo = {m1} que está na posição {pos}, e o Mínimo2 = {m2}")

lext = np.repeat(m1, n) # Repete m1 n vezes
lext[pos] = m2

lext = S * np.sign(l) * lext
L = l + lext

print("============================(n,n-1)==========================")

for i in range(n):
    print(f"l_ext({i}) = {lext[i]}, L({i}) = {L[i]}")

print("=============================================================")