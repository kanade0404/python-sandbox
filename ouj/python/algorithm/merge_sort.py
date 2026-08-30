from typing import List


import random as rd  # 動作テスト用に乱数ライブラリを使うので、先に宣言しておく


# マージソート を再帰（帰納的関数）で定義する
def msort(target: List[int]) -> List[int]:  # リスト target を整列して返す関数を定義する
    nagasa = len(target)
    if nagasa < 2:  # 大きさが 2 未満なら、そのまま返す。
        return target

    else:  # 大きさが2以上のときは、前後に分割して、それぞれを整列させてから、併合（マージ）する。
        lmae = int(nagasa / 2)  # 前側の長さを求めておく。
        lushiro = nagasa - lmae  # ushiro の長さを求めておく（後で使う）

        # target の前半分を mae に入れる
        mae = target[:lmae]
        mae = msort(mae)  # mae を整列する（再帰）
        # target の後半分を ushiro に入れる
        ushiro = target[lmae:]
        ushiro = msort(ushiro)  # ushiro を整列する（再帰）

        m = 0  # mae の m 番目を見ることにする
        u = 0  # ushiro の u 番目を見ることにする
        r = []  # 整列結果を r に入れることにする

        while m < lmae or u < lushiro:  # まだ整列が終わってないときに繰り返す
            if m == lmae:  # すでに mae をすべて見終わっていたら
                r += ushiro[u:]  # リスト uhsiro の残りをすべて r にくっつけて
                u = lushiro  # 後ろ側も終了にする
            elif (
                u == lushiro
            ):  # まだ mae を見終えていないが、すでに ushiro をすべて見終わっていたら
                r += mae[m:]  # リスト mae の残りをすべて r にくっつけて
                m = lmae  # 前側も終了にする
            else:  # mae も、 ushiro も、見終えてないときは
                if (
                    mae[m] < ushiro[u]
                ):  # それぞれで、まだ r に入れていない先頭の要素同士を比較して、maeが小さいなら
                    r.append(mae[m])  # r に、mae の先頭を追加して
                    m += 1  # m を一つ増やす
                else:
                    r.append(ushiro[u])  # r に、ushiro の先頭を追加して
                    u += 1  # u を一つ増やす
    # print("out: ", r)
    return r


# 0以上999以下の乱数を kosu 個生成してリストにする
kosu = 12
min_r, max_r = 0, 999

# min_r 以上 max_r以下の乱数を kosu個 生成する
narabi = [rd.randint(min_r, max_r) for _ in range(kosu)]

print("Before: " + str(narabi))  # 整列前を表示
narabi = msort(narabi)
print("After_: " + str(narabi))  # 整列後を表示
