# 素数であれば1、素数でなければ 0 を返す関数
def sosuka(p):
    i = 2
    flag = 1
    while i * i <= p:
        r = p % i
        if r == 0:
            flag = 0
        i += 1
        print("check")
    return flag


# 判定結果のメッセージ
kekka = ("gouseisu", "sosu")

# 入力させる
x = int(input("x?"))

# 出力
print(kekka[sosuka(x)])
