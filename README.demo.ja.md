# CoverShift デモ手順（11/20 中間発表用）

[한국어](README.demo.md) | [日本語](README.demo.ja.md)

ドラッグストアの月間シフトを、AIが「提案」し、コードが「検査」し、店長が「確定」するデモです。
**AIは提案だけ。最終確定は、人（店長）が画面で行います。**

> このブランチ（`feature/add-seunghyeok-cde`）は、アズキのグラフ・DB・API と、スンヒョクのサーバー・画面・検査を、1つにまとめたものです。`develop` にはまだ入っていません。

## 1. 何が動くか

```mermaid
flowchart LR
  W[画面 Vue<br/>127.0.0.1:5173] -->|/api| A[入口API FastAPI<br/>127.0.0.1:8010]
  A --> R[実行役 LocalRunner]
  R -->|GRAPH_IMPL=seung 既定| GS[スンヒョクのグラフ<br/>app/graph_seung]
  R -->|GRAPH_IMPL=azuki| B[橋 app/graph_azuki_bridge.py]
  B --> GA[アズキのグラフ<br/>app/graph]
  GS --> D[(PostgreSQL)]
  GA --> D
  GS -.->|LLM_MODE=fake / anthropic| L[AI]
  GA -.->|Claude| L
```

流れ: 希望の提出 → AIが案を作る → コードが検査（直らなければ作り直し、最大3回）→ 人が足りなければ担当者に打診 → 店長が確認・承認 → 確定 → スタッフへ通知（LINEは**スタブ**＝実際には送らず、画面に表示）。

## 2. 切り替えスイッチ

| 環境変数 | 値 | 意味 |
|---|---|---|
| `GRAPH_IMPL` | `seung`（既定） | スンヒョクのグラフを使う |
| | `azuki` | アズキのグラフを使う（橋が、画面・DBとの形を合わせる） |
| `LLM_MODE` | `fake` | 偽のAI（速い。お金がかからない）。スンヒョクのグラフ用 |
| `ENABLE_CONTACT` | `1` | 人数不足が残ったとき、担当者へ打診する（スンヒョクのグラフ用。アズキのグラフは常にオン） |

## 3. 必要なもの

- Python 3.13、Node 24 以上、Docker Desktop、Git
- Windows の場合、コマンドの前に `$env:PYTHONUTF8="1"`（文字化け・読み込みエラー防止）
- アズキのグラフで本物のClaudeを使うときだけ、`.env` に `ANTHROPIC_API_KEY`（**Gitに上げない・チャットに貼らない**）

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd web
npm install
```

## 4. 起動（ターミナル3つ）

### ① DBを用意する（最初の1回。やり直したいときも、これ）

```
docker compose -f compose.test.yaml -p mvp-test up -d --wait
$env:PYTHONUTF8="1"; python -m scripts.screen_dev_setup
```

- 普段のDB（5433）には触りません。テスト用Postgres（5443）に、`demo` というDBを作り直します。
- **このDBはメモリ上にあります。Dockerを止めると、消えます**（デモ用のダミーデータなので、これで構いません）。
- 確定済みの期間は、もう一度開始できません。もう一度試すときは、この手順をやり直します。

### ② API（ターミナル2）

**A. 偽のAI＋スンヒョクのグラフ（速い。画面の操作確認用）**

```
$env:PYTHONUTF8="1"; $env:DATABASE_URL="postgresql://mvp:mvp@127.0.0.1:5443/demo"; $env:JWT_SECRET="screen-only-secret-0123456789abcdefghij"; $env:DEV_SKIP_AUTH="false"; $env:RUNNER="local"; $env:MCP_MODE="inprocess"; $env:LLM_MODE="fake"; $env:FAKE_LLM_SCENARIO="greedy"; $env:GRAPH_IMPL="seung"; $env:ENABLE_CONTACT="1"
python -m uvicorn app.api_seung.main:app --port 8010
```

**B. アズキのグラフ＋本物のClaude（約7分かかる）**

```
$env:PYTHONUTF8="1"; $env:DATABASE_URL="postgresql://mvp:mvp@127.0.0.1:5443/demo"; $env:JWT_SECRET="screen-only-secret-0123456789abcdefghij"; $env:DEV_SKIP_AUTH="false"; $env:RUNNER="local"; $env:MCP_MODE="inprocess"; $env:LLM_MODE="fake"; $env:GRAPH_IMPL="azuki"
python -m uvicorn app.api_seung.main:app --port 8010
```

`JWT_SECRET` は、このデモ専用の使い捨ての値です（本番では使わない）。

### ③ 画面（ターミナル3）

```
cd web
npm run dev
```

ブラウザで http://127.0.0.1:5173 を開き、ログインします。

- メール: `demo@example.com`
- パスワード: `demo-password-1`（デモ用のダミー）

## 5. 画面での操作（A の場合）

| 順 | 操作 | 見ること |
|---|---|---|
| 1 | 「シフト期間」で2026年11月を開き、「今すぐ開始」 | 数秒で、打診待ちになる |
| 2 | 「LINE送信箱（スタブ）」を開く | 「勤務できますか?」と、返事ボタンが出る |
| 3 | 「不可」を押す | 次の人への打診（最大3人） |
| 4 | 次の打診で「可能」を押す | 承認待ちに進む |
| 5 | 「AIへの指示」を書いて「再編成を依頼する」 | 案が新しくなり、残り回数が減る |
| 6 | 「編集モードにする」で1マス直して保存 | 再検査される |
| 7 | 「承認して確定する」 | 確定済み。LINE送信箱に「シフトが確定しました」が並ぶ |

警告（人数不足など）は、**確認のみで、確定は止めません**。「必須」の違反（資格者なし・二重配置）が残っていると、承認できません。

## 6. テスト

テスト用DBを起動した状態で実行します。

```
$env:PYTHONUTF8="1"; python -m pytest tests/api tests/graph tests/test_e2e.py tests/test_e2e_azuki.py tests/test_review_fixes.py -q -p no:warnings
```

- 11/19 時点の結果: **104件成功、1件失敗**。失敗の1件（`test_aegra_entry_graph_runs_async_with_injected_checkpointer`）は、Windows の非同期の仕組み（ProactorEventLoop）が原因です。コードの不具合ではありません。
- `tests/test_e2e_azuki.py`: アズキのグラフを、API経由で通すテスト（承認〜確定、打診の返事）。偽のAIを使います。
- `tests/test_real_llm_azuki.py`: 本物のClaudeで1回通すテスト。**お金がかかる**ので、`RUN_REAL_LLM=1` のときだけ動きます。

```
$env:RUN_REAL_LLM="1"; python -m pytest tests/test_real_llm_azuki.py -q -s -p no:warnings
```

## 7. 実測と、既知の制約

**本物のClaudeの実測（アズキのグラフ、11/19、1回分）**

| 回 | 秒 | 出力トークン | 検査の結果 |
|---|---|---|---|
| 1回目 | 206 | 33,572 | 人数の問題5件 → 作り直し |
| 2回目 | 166 | 27,089 | 問題1件 → 作り直し |
| 3回目 | 56 | 11,521 | 有効 → 打診へ |

- 合計は約7分。時間は、AIが出力する量にほぼ比例しました（約160トークン/秒）。出力の約半分は、AIが「考える」分でした。
- 本物のClaudeは、実行のたびに結果が変わります。

**既知の制約**

- 7分の待ち時間は、デモ中に待てません。デモ前に1回通しておくことを、おすすめします。
- アズキのグラフ（B）では、画面の「AIのメモ」「人数が足りない時間帯（AIの報告）」が空になります。AIの返事の注記を、グラフが State に残していないためです。
- DBは、スンヒョクの `db/001`・`db/002` を前提にしています（`line_outbox`、`contact_log` の形など）。リーダーのDB案（2026-11-18）との整合は、まだ取れていません。
- LINEは**スタブ**です。実際のLINEには、送りません。
- 画面に出る文章は、日本語に直しました。ログ（開発者向け）には、韓国語が残っています。

## 8. 主なファイル

| 場所 | 中身 |
|---|---|
| `app/graph/` | アズキのグラフ（LangGraph）。**この中は、変えていません** |
| `app/graph_seung/` | スンヒョクのグラフ（安全網として残す） |
| `app/graph_azuki_bridge.py` | 橋。アズキのグラフを、画面・DB・実行役の形に合わせる |
| `app/graph_azuki_convert.py` | 橋の、値の変換（行の形、返事の形） |
| `app/api_seung/` | スンヒョクの入口API（画面がつなぐ先） |
| `app/validate/` | スンヒョクの検査（画面に出す違反一覧を作る） |
| `scripts/screen_dev_setup.py` | 画面用のDBを作り直す |
| `web/` | 画面（Vue） |
| `tests/` | テスト一式 |
