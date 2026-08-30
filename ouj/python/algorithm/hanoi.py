from typing import NamedTuple


class Pillars(NamedTuple):
    p0: str
    p1: str
    p2: str


def hanoi(n: int, pillars: Pillars):
    if n == 1:
        print("円盤", n, ":", pillars.p0, "->", pillars.p1, ".")
    else:
        hanoi(n - 1, Pillars(pillars.p0, pillars.p2, pillars.p1))
        print("円盤", n, ":", pillars.p0, "->", pillars.p1, ".")
        hanoi(n - 1, Pillars(pillars.p2, pillars.p1, pillars.p0))


k = int(input("円盤の枚数?"))

hanoi(k, Pillars("A", "B", "C"))
