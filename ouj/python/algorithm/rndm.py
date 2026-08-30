import random as rd

n = 1000
incount = 0
count = 0
for i in range(n):
    x = rd.random()
    y = rd.random()
    count += 1
    print("check:", count)
    if x + y < 1:
        incount += 1
print("kekka =", incount / n)
