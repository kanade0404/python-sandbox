def lucas(k: int):
    print("check")
    if k == 0:
        return 2
    elif k == 1:
        return 1
    else:
        return lucas(k - 1) + lucas(k - 2)


i = int(input("i?"))
print(lucas(i))
