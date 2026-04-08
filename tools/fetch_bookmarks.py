"""X (Twitter) のブックマークを取得して bookmarks/ 配下に保存するスクリプト.

使い方:
    python tools/fetch_bookmarks.py

初回はブラウザで認可ダイアログが開く. 2回目以降は refresh_token で自動更新.
取得結果は bookmarks/latest.md (Markdown) と bookmarks/{timestamp}_raw.json (生データ) に保存.

事前準備:
    1. X Developer Portal で Project & App を作成 (Basic tier 以上が必要)
    2. OAuth 2.0 を有効化, Type=Confidential client, Callback URL に
       http://127.0.0.1:8080/callback を登録
    3. Client ID と Client Secret を .env に書く
    4. uv venv .venv --python 3.11 && uv pip install -r requirements.txt
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ.get("X_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("X_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("X_REDIRECT_URI", "http://127.0.0.1:8080/callback")

SCOPES = ["bookmark.read", "tweet.read", "users.read", "offline.access"]
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
USERS_ME_URL = "https://api.x.com/2/users/me"
BOOKMARKS_URL_TMPL = "https://api.x.com/2/users/{uid}/bookmarks"

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = REPO_ROOT / ".x_tokens.json"
OUT_DIR = REPO_ROOT / "bookmarks"
SEEN_FILE = OUT_DIR / ".seen_ids.json"


def _basic_auth() -> str:
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _make_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _post_token(payload: dict) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data=payload,
        headers={
            "Authorization": _basic_auth(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Token endpoint {resp.status_code}: {resp.text}")
    tokens = resp.json()
    tokens["expires_at"] = int(time.time()) + int(tokens.get("expires_in", 7200)) - 60
    return tokens


def run_oauth_flow() -> dict:
    verifier, challenge = _make_pkce()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"

    received: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received["code"] = qs.get("code", [None])[0]
            received["state"] = qs.get("state", [None])[0]
            received["error"] = qs.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "OK. このタブは閉じてターミナルに戻ってや" if received["code"] else f"NG: {received.get('error')}"
            self.wfile.write(f"<h1>{msg}</h1>".encode())

        def log_message(self, *args):  # noqa: ANN002
            pass

    parsed = urllib.parse.urlparse(REDIRECT_URI)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8080
    server = http.server.HTTPServer((host, port), Handler)

    print(f"\nブラウザで認可ダイアログを開くで:\n  {auth_url}\n")
    webbrowser.open(auth_url)
    print(f"  {host}:{port} で待機中...")
    server.handle_request()
    server.server_close()

    if received.get("error"):
        raise RuntimeError(f"Authorization error: {received['error']}")
    if received.get("state") != state:
        raise RuntimeError("CSRF state mismatch")
    if not received.get("code"):
        raise RuntimeError("認可コードが取れんかった")

    return _post_token(
        {
            "grant_type": "authorization_code",
            "code": received["code"],
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        }
    )


def refresh_tokens(refresh_token: str) -> dict:
    return _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
    )


def get_tokens() -> dict:
    if TOKEN_FILE.exists():
        tokens = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        if tokens.get("expires_at", 0) > time.time():
            return tokens
        if tokens.get("refresh_token"):
            try:
                print("access_token 期限切れ → refresh_token で更新中...")
                tokens = refresh_tokens(tokens["refresh_token"])
                TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
                return tokens
            except Exception as e:
                print(f"refresh失敗: {e}\n再認可するで")

    tokens = run_oauth_flow()
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    return tokens


def fetch_bookmarks(access_token: str) -> tuple[list, dict, dict]:
    headers = {"Authorization": f"Bearer {access_token}"}

    me = requests.get(USERS_ME_URL, headers=headers, timeout=30)
    me.raise_for_status()
    user_id = me.json()["data"]["id"]
    print(f"自分のuser_id: {user_id}")

    url = BOOKMARKS_URL_TMPL.format(uid=user_id)
    base_params = {
        "max_results": 100,
        "expansions": "author_id,attachments.media_keys,referenced_tweets.id",
        "tweet.fields": "created_at,public_metrics,entities,lang,referenced_tweets,attachments",
        "user.fields": "username,name,profile_image_url",
        "media.fields": "url,preview_image_url,type,alt_text",
    }

    tweets: list = []
    users: dict = {}
    media: dict = {}
    page = 0
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
        page += 1
        page_tweets = data.get("data", []) or []
        tweets.extend(page_tweets)
        for u in data.get("includes", {}).get("users", []):
            users[u["id"]] = u
        for m in data.get("includes", {}).get("media", []):
            media[m["media_key"]] = m

        print(f"  page {page}: +{len(page_tweets)} (total {len(tweets)})")

        pagination_token = data.get("meta", {}).get("next_token")
        if not pagination_token:
            break
        time.sleep(1)

    return tweets, users, media


def load_seen_ids() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        return set(data.get("ids", []))
    except Exception as e:
        print(f"  warn: .seen_ids.json 読込失敗 ({e}). 全件を新着扱いにする")
        return set()


def save_seen_ids(ids: set[str]) -> None:
    SEEN_FILE.write_text(
        json.dumps({"ids": sorted(ids), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2),
        encoding="utf-8",
    )


def render_markdown(tweets: list, users: dict, media: dict) -> str:
    lines = ["# X Bookmarks", ""]
    lines.append(f"取得日時: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"件数: {len(tweets)}")
    lines.append("")
    lines.append("> **注意**: ここに含まれるツイートは第三者の著作物。公開リポにはコミットせえへんように。")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, t in enumerate(tweets, 1):
        author = users.get(t.get("author_id", ""), {})
        username = author.get("username", "unknown")
        name = author.get("name", "")
        tid = t["id"]
        url = f"https://x.com/{username}/status/{tid}"

        lines.append(f"## {i}. @{username} — {name}")
        lines.append("")
        lines.append(t.get("text", "").strip())
        lines.append("")

        meta_bits = []
        if created := t.get("created_at"):
            meta_bits.append(f"投稿: {created}")
        metrics = t.get("public_metrics", {}) or {}
        if metrics:
            meta_bits.append(
                f"♥{metrics.get('like_count', 0)} "
                f"RT{metrics.get('retweet_count', 0)} "
                f"💬{metrics.get('reply_count', 0)}"
            )
        if meta_bits:
            lines.append("- " + " | ".join(meta_bits))
        lines.append(f"- {url}")

        for mk in (t.get("attachments", {}) or {}).get("media_keys", []) or []:
            m = media.get(mk, {})
            mtype = m.get("type", "")
            mu = m.get("url") or m.get("preview_image_url") or ""
            if mu:
                lines.append(f"- [{mtype}] {mu}")

        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: .env に X_CLIENT_ID と X_CLIENT_SECRET を設定してや", file=sys.stderr)
        print("       .env.example を参考にして .env を作る", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(exist_ok=True)

    seen_ids = load_seen_ids()
    print(f"前回までの既見: {len(seen_ids)} 件")

    tokens = get_tokens()
    tweets, users, media = fetch_bookmarks(tokens["access_token"])

    current_ids = {t["id"] for t in tweets}
    new_ids = current_ids - seen_ids
    new_tweets = [t for t in tweets if t["id"] in new_ids]

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    raw_path = OUT_DIR / f"{timestamp}_raw.json"
    raw_path.write_text(
        json.dumps({"tweets": tweets, "users": users, "media": media}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = render_markdown(tweets, users, media)
    md_path = OUT_DIR / f"{timestamp}.md"
    md_path.write_text(md, encoding="utf-8")
    (OUT_DIR / "latest.md").write_text(md, encoding="utf-8")

    new_md = render_markdown(new_tweets, users, media)
    (OUT_DIR / "new.md").write_text(new_md, encoding="utf-8")

    save_seen_ids(current_ids | seen_ids)

    print(f"\n[OK] 取得完了: 全{len(tweets)}件 / 新着{len(new_tweets)}件")
    print(f"  - {md_path.relative_to(REPO_ROOT)}")
    print(f"  - {raw_path.relative_to(REPO_ROOT)}")
    print(f"  - bookmarks/latest.md (全件)")
    print(f"  - bookmarks/new.md (新着のみ)")
    print("\nClaude Code で `/read-bookmarks` 叩くとねーさんが読んでくれるで")
    return 0


if __name__ == "__main__":
    sys.exit(main())
