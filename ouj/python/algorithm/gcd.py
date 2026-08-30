from typing import Optional

x: int = int(input("x?"))
y: int = int(input("y?"))

if __name__ == "__main__":
    d: Optional[int] = None
    while x > 0 and y > 0:
        if x > y:
            x = x % y
            d = y
        else:
            y = y % x
            d = x
        print(x, y, d)

    print("answer =", d)
