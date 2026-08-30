import matplotlib.pyplot as plt

if __name__ == "__main__":
    # 状況の設定
    suzuran = 90
    kasumi = 140
    a_suzuran, a_kasumi, a_price = 3, 5, 300
    b_suzuran, b_kasumi, b_price = 2, 3, 190

    p_saidai, a_saidai, b_saidai = 0, 0, 0
    # グラフを描くために、まず、縦軸 p のリストを用意する
    p = []

    # 花束セットA が 0 と仮定して開始する
    hana_a = 0

    while suzuran >= 0 and kasumi >= 0:  # 花が残っている限り、繰り返しを続ける
        hana_b = min(
            int(suzuran / b_suzuran), int(kasumi / b_kasumi)
        )  # 現在の花で作れるセットBの数
        price = a_price * hana_a + b_price * hana_b  # 現在の売上合計
        print("A = ", hana_a, ", B = ", hana_b, ", P = ", price)  # 確認用
        p.append(price)  # 価格を追加
        if price > p_saidai:  # 売上合計が、それまでより高額であれば
            p_saidai, a_saidai, b_saidai = (
                price,
                hana_a,
                hana_b,
            )  # 最大値となっているところを記録
        hana_a += 1  # 次の繰り返しのためにセットA をひとつ増やす
        suzuran -= a_suzuran  # 次の繰り返しのためにセットA をひとつ分のすずらんを使う
        kasumi -= a_kasumi  # 次の繰り返しのためにセットA をひとつ分のかすみ草を使う

    plt.xlabel("A")
    plt.ylabel("price")
    plt.plot(p)
    print("Result: A = ", a_saidai, ", B = ", b_saidai, ", P = ", p_saidai)
