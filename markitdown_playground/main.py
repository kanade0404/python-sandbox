import requests
from markitdown import MarkItDown
import os

# Apify Proxy設定
APIFY_PROXY_URL = os.getenv("APIFY_PROXY_URL")
APIFY_PROXY_USERNAME = os.getenv("APIFY_PROXY_USERNAME")
APIFY_PROXY_PASSWORD = os.getenv("APIFY_PROXY_PASSWORD")

# ユーザーエージェント設定
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36"


def create_session_with_proxy_and_user_agent():
    """プロキシとユーザーエージェントを設定したrequestsセッションを作成"""
    session = requests.Session()

    # プロキシ設定
    proxies = {
        "http": f"http://{APIFY_PROXY_USERNAME}:{APIFY_PROXY_PASSWORD}@{APIFY_PROXY_URL}",
        "https": f"http://{APIFY_PROXY_USERNAME}:{APIFY_PROXY_PASSWORD}@{APIFY_PROXY_URL}",
    }
    session.proxies.update(proxies)

    # ヘッダー設定（ユーザーエージェント）
    headers = {"User-Agent": USER_AGENT}
    session.headers.update(headers)

    return session


def fetch_markdown_with_markitdown(url, session):
    """MarkItDownを使ってURLの内容をMarkdownに変換"""
    markitdown = MarkItDown(requests_session=session)  # セッションを渡す
    result = markitdown.convert(url)  # URLを直接渡す
    return result.text_content


def main():
    # セッション作成
    # session = create_session_with_proxy_and_user_agent()

    # Markdown取得
    try:
        markdown_content = fetch_markdown_with_markitdown(
            "https://oceans-nadia.com/user/561742/recipe/491310", requests.Session()
        )
        print(markdown_content)

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
