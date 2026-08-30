if __name__ == "__main__":
    # 平方根の計算のために数学計算ライブラリを呼び出す
    import math

    m = 200  # どこまで求めるか

    a = list(
        range(m + 1)
    )  # リスト a を用意する。すべての要素に、添字と同じ値を代入して初期化しておく。
    limit = int(math.sqrt(m))  # ルート m まで調べたら終了
    sosu = []  # 最後に素数一覧を入れるリストを用意しておく

    print(
        "init =", a[2:]
    )  # 最初はすべての数が残っている状態を確認。ただし、0, 1 は除くのでスライスで除去
    print("")

    for i in range(2, limit + 1):  # i を、 2 から limit まで変化させる
        if not (a[i] == ""):  # もし、i が素数と確定していたら
            print(i, " ", end="")  # 見つけた素数を表示
            j = i + i  # j を合成数として消去するため、まず、 j=2i とする
            while j <= m:
                a[j] = ""  # j を合成数として消去する
                j += i  # j=2i, 3i, 4i, ....  と増やしていく
            print(a[2:])  # 消した結果を表示
            print("")  # 見易いように1行空ける

    # 素数判定が終わったら、合成数として消去されてない数をリスト sosu にまとめる
    for i in range(2, m + 1):
        if not (a[i] == ""):
            sosu.append(i)

    print("sosu =", sosu)
