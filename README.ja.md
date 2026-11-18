# CoverShift Backend (FastAPI)

[한국어](README.md) | [日本語](README.ja.md)

## 必要なもの

- Python 3.13（アズキの環境で確認済み）
- Docker Desktop
- Git

## 最初のセットアップ

1. 仮想環境を作る
   - Windows: `python -m venv .venv` → `.venv\Scripts\activate`
   - Mac: `python3 -m venv .venv` → `source .venv/bin/activate`
2. パッケージを入れる: `pip install -r requirements.txt`
3. 環境変数ファイルを作る（`.env` は Git に上げない）
   - Windows: `copy .env.example .env`
   - Mac: `cp .env.example .env`
   - ⚠️ `.env` が既にあると上書きされる。最初の1回だけ実行する。
   - `ANTHROPIC_API_KEY` は各自で入れる
   - `JWT_SECRET`（ログイン用の署名キー）は、各自で長いランダム文字列を入れる。共有しない。チャットやPRに貼らない。
     - 作り方の例: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - ログイン機能（E9）ができるまでは、`DEV_SKIP_AUTH=1` にすると、ログインなしでAPIを試せる。**開発中だけ使う。本番では入れない。**
4. DBを起動する: `docker compose up -d db`
5. スキーマを適用する（`<スキーマファイル>` は `db/` フォルダの最新のDDLのファイル名に置き換える）

```
   docker compose cp db/<スキーマファイル> db:/tmp/schema.sql
   docker compose exec db psql -U covershift -d covershift -v ON_ERROR_STOP=1 -f /tmp/schema.sql
```

6. 最初の店舗と管理者を作る（最初の1回だけ。パスワードは実行中に入力する）

```
   python -m scripts.bootstrap store "店名" manager@example.com
   python -m scripts.bootstrap invite <store_id>
```

7. サーバーを起動する: `uvicorn app.main:app --reload`
8. 確認する
   - http://127.0.0.1:8000/health → `{"ok":true}`
   - http://127.0.0.1:8000/docs → API一覧（Swagger）

## APIの構成

- すべてのAPIは `/api` の下にある。例: `GET /api/employees`
- エラーの形は統一されている: `{"error":{"code","message","details?"}}`
- ログインが必要なAPIは `Authorization: Bearer <JWT>` が必要（`DEV_SKIP_AUTH=1` なら省略できる）
- リンク（トークン）で使うAPI（`/api/submit/{token}`、`/api/line/link`）は、ログイン不要
- ログイン・会員登録（register/login/logout）は E9（ヒョクジュン担当）で作る。このリポジトリにはまだ無い。
- 保留・後回しのAPIは入口だけあり、501を返す。廃止したAPIは410を返す。
- グラフ（LangGraph）との接続口は `GraphGateway`。接続前は、編成開始APIが503を返す。

## MCP（D5）：グラフが従業員データを読む道

- 従業員データ（希望シフトなど）は、グラフの `collect` が、MCPサーバーの道具を呼んで読む。DBを読むのはMCPサーバーの中だけ。
- ファイル
  - `app/mcp/mcp_server.py` … MCPサーバー。道具は `get_staff_availability` の1つだけ。読むだけで、書き込みの道具は無い。名前（`employees.name`）は返さない。
  - `app/mcp/mcp_client.py` … グラフから道具を呼ぶ部品。LLM に見せる道具は `LLM_TOOLS` に入れたものだけ（いまは空）。
- 道具の一覧

| 道具 | 触るもの | 呼ぶ人 | LLM に見せるか |
|---|---|---|---|
| `get_staff_availability` | DB（読むだけ） | コード（`collect`） | 見せない |

- 環境変数 `STAFF_SOURCE`（`period_id` があるときの、従業員データの読み先）
  - `mcp`（既定）… MCPサーバー経由
  - `db` … DBを直接読む（結果は `mcp` と同じ。`test_mcp.py` の M5a で確認）
- MCPサーバーには `DATABASE_URL` だけを渡す。`ANTHROPIC_API_KEY` などは渡さない。
- `mcp` パッケージが必要（2.3.0 で確認）。
- テスト: `test\graph` フォルダで `python test_mcp.py`（環境変数 `TEST_DATABASE_URL` が必要。無ければ SKIP して終わる）

## 注意

- DBのポートは **5433**。（アズキのWindowsでは、PostgreSQLが既に5432を使っていたため）
  5432が空いているPCでは、`docker-compose.yml` の `ports` と `.env` の `DATABASE_URL` を一緒に変えてもよい。
- すべてのコマンドは、`docker-compose.yml` があるフォルダで実行する。
- `manager_actions` テーブルは、まだDBに無い。追加されるまで、初稿の承認・差し戻し・手修正のAPIは動かない。

## DBを最初から作り直す（データはすべて消える）

```
docker compose exec db psql -U covershift -d covershift -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

そのあと、5番（スキーマ適用）と6番（店舗・管理者の作成）をもう一度実行する。
