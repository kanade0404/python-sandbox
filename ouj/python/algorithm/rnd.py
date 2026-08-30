import random as rd  # 乱数を発生させる関数の呼び出し
import matplotlib.pyplot as plt  # グラフプロットの呼び出し

if __name__ == "__main__":
    incount = 0  # 円に入った点の数
    n = int(input("How many points do you use?"))  # ランダムに打つ点の総数
    plt.figure(figsize=(5, 5))
    for i in range(n):
        x = rd.random()  # 0-1 の範囲の値
        y = rd.random()  # 0-1 の範囲の値
        if x * x + y * y < 1.0:  # 単位円の中に入ったら
            incount += 1  # 入ったカウンターに１を加える
            plt.scatter(x, y, c="#000")  # 黒色でプロット
        else:
            plt.scatter(x, y, c="#aaa")  # 灰色でプロット
    p = 4 * incount / n
    print(" 円周率:", p)  # 求まった円周率の近似値
    plt.title("Monte Carlo method")  # グラフのタイトル
    plt.show()
