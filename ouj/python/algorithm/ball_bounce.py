if __name__ == "__main__":
    # x, y のリストに append を使わないプログラム
    # matplotlib で描画
    import math as math  # 数値計算ライブラリ
    import matplotlib.pyplot as plt  # グラフ描画ライブラリ

    span = 20  # 継続秒数
    dt = 0.01  # 微小時間 (時間間隔)
    v0 = 30.0  # 初速度
    g = 9.8  # 重力加速度
    times = int(
        0.5 + span / dt
    )  # 繰り返し回数 2進法←→10進法 変換のせいで整数でなくなるときがあるので int で整数化する
    x = [0.0] * (1 + times)  # 水平位置の初期値 (0.0) で、長さ 1 + times のリストを作る
    y = [0.0] * (1 + times)  # 垂直位置の初期値 (0.0) で、長さ 1 + times のリストを作る
    h = 0.7  # 反発係数
    angle = 45.0 * math.pi / 180.0  # 投げ上げ角度
    x_size, y_size, zoom = 16, 2, 20  # 描画領域の縦横の大きさとズーム率

    vx = v0 * math.cos(angle)  # 水平方向の初速度
    vy = v0 * math.sin(angle)  # 鉛直方向の初速度

    for t in range(times):  # step t から step t+1 への位置を計算する
        x[t + 1] = x[t] + vx * dt  # 水平方向の新しい位置を リスト x に追加

        new_vy = vy - g * dt  # 微小時間後の鉛直方向の速度を仮計算する
        new_y = y[t] + (vy + new_vy) / 2.0 * dt  # 微小時間後の鉛直位置を仮計算する

        vy = new_vy  # 地面にぶつかってないとして、微小時間後の鉛直方向の速度を設定する
        if (
            new_y < 0
        ):  # もし微小時間後の鉛直位置の仮計算値が 0 より小さいなら地面に当たっていることになるので
            vy = -new_vy * h  # 反発係数 h で反発させる
            new_y = -new_y  # 反発したあとの位置
        y[t + 1] = new_y  # 微小時間後の鉛直位置をリスト y に追加

    plt.figure(figsize=[x_size, y_size])
    plt.plot(x, y)  # 位置の配列をプロット
    plt.title("parabollic motion")  # グラフのタイトル
    plt.xlabel("distance")  # x 軸ラベル
    plt.ylabel("height")  # y 軸ラベル
    plt.xlim(0, zoom * x_size)  # 描画領域 x 方向
    plt.ylim(0, zoom * y_size)  # 描画領域 y 方向
