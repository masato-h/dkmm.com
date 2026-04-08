"""自分のツイートを取得して tweets/ 配下に保存するスクリプト.

使い方:
    python tools/fetch_my_tweets.py

fetch_bookmarks.py と同じ OAuth トークンを流用する.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

# fetch_bookmarks と同じトークンファイルを流用
REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = REPO_ROOT / ".x_tokens.json"
OUT_DIR = REPO_ROOT / "tweets"

USERS_ME_URL = "https://api.x.com/2/users/me"
USER_TWEETS_URL_TMPL = "https://api.x.com/2/users/{uid}/tweets"


sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_bookmarks import get_tokens  # noqa: E402


def fetch_my_tweets(access_token: str) -> tuple[list, dict, dict]:
    headers = {"Authorization": f"Bearer {access_token}"}

    me = requests.get(USERS_ME_URL, headers=headers, timeout=30)
    me.raise_for_status()
    user_id = me.json()["data"]["id"]
    print(f"user_id: {user_id}")

    url = USER_TWEETS_URL_TMPL.format(uid=user_id)
    base_params = {
        "max_results": 100,
        "expansions": "attachments.media_keys,referenced_tweets.id",
        "tweet.fields": "created_at,public_metrics,entities,lang,referenced_tweets,attachments",
        "user.fields": "username,name",
        "media.fields": "url,preview_image_url,type",
        "exclude": "retweets,replies",
    }

    tweets: list = []
    media: dict = {}
    pagination_token: str | None = None

    while True:
        params = dict(base_params)
        if pagination_token:
            params["pagination_token"] = pagination_token

        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            wait = max(reset - int(time.time()), 5)
            print(f"  rate limit hit. {wait}s 待機...")
            time.sleep(wait)
            continue
        resp.raise_for_status()

        data = resp.json()
        page_tweets = data.get("data", []) or []
        tweets.extend(page_tweets)
        for m in data.get("includes", {}).get("media", []):
            media[m["media_key"]] = m

        print(f"  +{len(page_tweets)} (total {len(tweets)})")

        pagination_token = data.get("meta", {}).get("next_token")
        if not pagination_token:
            break
        time.sleep(1)

    return tweets, media


def render_markdown(tweets: list, media: dict) -> str:
    lines = ["# My Tweets", ""]
    lines.append(f"取得日時: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"件数: {len(tweets)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, t in enumerate(tweets, 1):
        metrics = t.get("public_metrics", {})
        likes = metrics.get("like_count", 0)
        rts = metrics.get("retweet_count", 0)
        replies = metrics.get("reply_count", 0)

        lines.append(f"## {i}.")
        lines.append("")
        lines.append(t.get("text", ""))
        lines.append("")
        lines.append(f"- 投稿: {t.get('created_at', '?')} | {likes=} {rts=} {replies=}")

        # media
        for mk in (t.get("attachments") or {}).get("media_keys", []):
            m = media.get(mk, {})
            url = m.get("url") or m.get("preview_image_url", "")
            if url:
                lines.append(f"- [{m.get('type', 'media')}] {url}")

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tokens = get_tokens()
    tweets, media = fetch_my_tweets(tokens["access_token"])

    if not tweets:
        print("ツイートが見つからんかった")
        return 0

    ts = time.strftime("%Y%m%d_%H%M%S")
    raw_path = OUT_DIR / f"{ts}_raw.json"
    raw_path.write_text(json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8")

    md = render_markdown(tweets, media)
    (OUT_DIR / "latest.md").write_text(md, encoding="utf-8")

    print(f"\n[OK] 取得完了: {len(tweets)}件")
    print(f"  - {raw_path.relative_to(REPO_ROOT)}")
    print(f"  - tweets/latest.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
