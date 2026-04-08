# tools

このリポで使う自動化スクリプト置き場。

## fetch_bookmarks.py — Xブックマーク取得

X (Twitter) のブックマークを API 経由で取得し、`bookmarks/` 配下に Markdown と
JSON で保存するスクリプト。LLM に対話的に読ませる前提。

### 事前準備

#### 1. X Developer Console の設定

> **2026/2 以降の料金体系**: Free/Basic/Pro tier は新規申込不可になり、
> **Pay-Per-Use（従量課金）がデフォルト** に変わってる。bookmarks 取得も
> 従量課金で利用可能（post read 単価ベース）。実行前に必ず Console で
> **spending limit を設定** すること。

1. [Developer Console](https://console.x.com/) で Project & App を作成
2. App の **User authentication settings** で OAuth 2.0 を有効化
3. **Type of App** = `Confidential client` を選択
4. **Callback URI / Redirect URL** に以下を登録（一字一句完全一致必須）:
   ```
   http://127.0.0.1:8080/callback
   ```
5. 必須 scope: `bookmark.read`, `tweet.read`, `users.read`, `offline.access`
6. **Client ID** と **Client Secret** を控える
7. **Billing → Spending limits** で月の上限を設定（事故防止）

#### 2. .env 設定

```bash
cp .env.example .env
# .env を編集して以下を埋める
#   X_CLIENT_ID=...
#   X_CLIENT_SECRET=...
```

#### 3. Python環境

```bash
uv venv .venv --python 3.11
uv pip install -r requirements.txt
```

### 実行

```bash
.venv/Scripts/python.exe tools/fetch_bookmarks.py     # Windows
# or
.venv/bin/python tools/fetch_bookmarks.py             # Unix
```

**初回のみ**: ブラウザで認可ダイアログが開くので承認。
`http://127.0.0.1:8080/callback` で待機しているローカルサーバーが
コードを受け取って `.x_tokens.json` に保存する。

**2回目以降**: `.x_tokens.json` の refresh_token で自動更新（access_token は2時間で切れる）。

### 出力

- `bookmarks/latest.md` — 最新の全ブックマーク（Markdown、ねーさん読み込み用）
- `bookmarks/{YYYYMMDD_HHMMSS}.md` — 取得時刻スナップショット
- `bookmarks/{YYYYMMDD_HHMMSS}_raw.json` — API レスポンス生データ

`bookmarks/` は **gitignore済み**。第三者のツイート本文を含むので公開リポには
コミットしない。気になったやつを自分の言葉でまとめたら `notes/` 側に書く。

### LLM に読ませる

Claude Code セッションで:
```
/read-bookmarks
```
これで latest.md が読み込まれて、ねーさんが対話モードで内容を語ってくれる。

### 制限事項・コスト感

- **取得は最新800件まで**（X API の仕様）
- **従量課金**: bookmarks 取得は post read 扱い。仮に $0.005/post なら 800件で $4 程度。
  正確な単価は Console 内でのみ確認可能。実行前に spending limit 設定推奨。
- レート制限: 180 req / 15min
- ページネーションが途中で止まるバグ報告あり（既知）
- access_token 有効期限 2時間 → refresh_token (有効期限なし) で延長
- 24時間UTC内の同一リソース重複請求は自動排除される
