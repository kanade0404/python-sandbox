def sosuka(p):
    flag = 1
    i = 2
    count = 1
    while i < p:
        print(count)
        count += 1
        r = p % i
        print(r, i)
        if r == 0:
            print("flag 0 ", i)
            flag = 0
        i += 1
    return flag


print(sosuka(int(input("x?"))))
