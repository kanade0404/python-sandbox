if __name__ == "__main__":
    # 得票状況と議席数の設定
    tokuhyo = [["A", 1200], ["B", 660], ["C", 1440], ["D", 180]]
    giseki = 6

    # 初期化
    tousu = len(tokuhyo)  # 党数
    hikaku = [0] * tousu  # 比較検討リスト
    tousenkei = 0  # すでに当選が決まった人数

    # 各党に、当選数を末尾に初期化（全党 0 にする）して追加
    for kakutou in tokuhyo:
        kakutou.append(0)

    # 比較検討リストに、得票数を複写
    for m in range(tousu):
        hikaku[m] = tokuhyo[m][1]

    # 当選数が議席数を下回っている間、続ける
    while tousenkei < giseki:
        print(tokuhyo, hikaku)
        # 比較検討リストの最大を探すために max を 0
        max = 0
        # 比較検討リストの最大 hikaku[maxi] の maxi を探して当選にする
        for i in range(tousu):
            if max < hikaku[i]:
                max = hikaku[i]
                maxi = i
        # 当選が決まったら割り当てる
        tokuhyo[maxi][2] = tokuhyo[maxi][2] + 1
        # 合計を増やす
        tousenkei = tousenkei + 1
        # 比較検討リストを更新する
        hikaku[maxi] = tokuhyo[maxi][1] / (tokuhyo[maxi][2] + 1)

    # 結果の表示
    print(tokuhyo, hikaku)
