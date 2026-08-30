import os

from apify import Actor
import asyncio
import requests


async def main():
    async with Actor:
        # プロキシ設定
        proxy_configuration = await Actor.create_proxy_configuration(
            groups=["RESIDENTIAL"],  # 使用するプロキシグループ
            country_code="US",  # 国コード（オプション）
            password=os.getenv("APIFY_PROXY_PASSWORD"),
        )

        # プロキシURLを取得
        proxy_url = await proxy_configuration.new_url()

        # requests用プロキシ設定
        proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

        # テストリクエスト
        response = requests.get(
            "https://api.apify.com/v2/browser-info", proxies=proxies
        )
        print(response.json())


# 非同期関数の実行
asyncio.run(main())
