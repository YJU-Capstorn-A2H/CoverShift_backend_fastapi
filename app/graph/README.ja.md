# CoverShift Backend (FastAPI)

# app/graph — シフト編成グラフ

[한국어](README.md) | [日本語](README.ja.md)

> **AIは提案だけ。最終確定は人(店長)。**
> 元の設計は `データフロー.md`。食い違ったら、そちらが正です。

LangGraph で作った、月間シフト編成のグラフです。
希望を集める → AI(LLM)が案を作る → **コードが**ルールを検査する → 店長が承認して確定する、という流れです。
(ルールを守れているかを決めるのは、LLM ではなく、コードです。)

## 1. ファイルの役割

| ファイル          | 役割                                                                                    |
| ----------------- | --------------------------------------------------------------------------------------- |
| `shift_graph.py`  | **実行の入口だけ**。`builder` を外に見せる。`python shift_graph.py start` で動かす      |
| `graph.py`        | **処理の中身**。ノード(collect〜send_final_shift)と、つなぎ(`builder`)                  |
| `routes.py`       | 分岐 `route_2`・`route_3`(次にどこへ行くかだけを決める)                                 |
| `state.py`        | `State`(グラフ全体で共有する箱)                                                         |
| `settings.py`     | 定数・店舗ごとの設定(`STORE_SETTINGS`)・スタッフデータ(`staff_data.json`。無くてもよい)・`get_store` |
| `node/convert.py` | DBから読んだ行を、グラフ用のデータに変える(**DBにはつながない**。変換だけ) |
| `node/db.py`      | DBから、1か月分のデータを読む(**読むだけ。書き込まない**)。`period_id` があれば `collect` が使う |
| `node/line.py`    | LINE で送る部品(スタブ。**本物は送らない**。送った内容を控えるだけ)。`send_final_shift` が使う |
| `node/checks.py`  | ルールの検査と計算。**LLMを使わない、コードだけの検査**                                 |
| `node/llm.py`     | LLM の用意と、返事からのJSON取り出し                                                    |
| `node/prompts.py` | LLM に渡す文章(`rules` と `make_*_text`)                                                |

読み込みの向き(循環しないように一方通行): `settings` ← `state` / `node.checks` ← `routes` ← `graph` ← `shift_graph`
DB まわりの向き: `node.convert` ← `node.db` ← `graph`
LINE まわりの向き: `node.line` ← `graph`

## 2. 処理番号と、ファイル・関数の対応

設計書・コメントにある番号(`2-1` など)から、コードの場所を探すための表です。**番号は、処理の流れの順**です。

| 番号 | 内容                                   | 種類      | ファイル             | 関数・もの                                      | 旧番号※        |
| ---- | -------------------------------------- | --------- | -------------------- | ----------------------------------------------- | -------------- |
| 0    | 初期設定                               | -         | `settings.py`        | 定数、`STORE_SETTINGS`、スタッフデータ          | 0              |
| 1    | State の箱                             | -         | `state.py`           | `State`                                         | 1              |
| 2-1  | 希望・指示を読む               | コード    | `graph.py`           | `collect`                                       | 2-1            |
| 2-4  | AIが編成案を作る                       | LLM       | `graph.py`           | `auto_draft`                                    | 2-5            |
| 2-5  | 各ルールの検査                         | コード    | `graph.py`           | `validate`                                      | 2-6            |
| 2-6  | 検査の結果で、次の行き先を決める       | **分岐②** | `routes.py`          | `route_2`                                       | 2-6c           |
| 2-8  | 人が足りない日の連絡(LINE送信は未実装) | 待機      | `graph.py`           | `contact`                                       | 2-8            |
| 2-9  | 管理者の承認                           | 待機      | `graph.py`           | `approve`                                       | 2-9            |
| 2-10 | 店長の決定で、次の行き先を決める       | **分岐③** | `routes.py`          | `route_3`                                       | 2-10           |
| 2-11 | 確定を保存(仮実装)                     | コード    | `graph.py`           | `finalize`                                      | 2-11           |
| 2-12 | 各従業員にLINEで通知(送る部品はスタブ) | コード    | `graph.py`           | `send_final_shift`                              | 2-13           |
| 3-1  | 検査と計算の道具                       | コード    | `node/checks.py`     | `check_*`、`to_min`、`build_contact_queue` など | 2-4, 2-6, 2-6b |
| 3-2  | LLMの用意・JSONの取り出し              | コード    | `node/llm.py`        | `get_llm`、`extract_json`                       | 2-4            |
| 3-3  | LLMに渡す文章                          | コード    | `node/prompts.py`    | `rules`、`make_*_text`                          | 2-5, 2-6a      |
| 3-4  | DBの行を、グラフ用のデータに変える     | コード    | `node/convert.py`    | `make_staff_list`、`make_store_settings` など   | -              |
| 3-5  | DBから1か月分を読む(読むだけ)          | コード    | `node/db.py`         | `load_period_data`                              | -              |
| 3-6  | LINEで1人に送る(スタブ)                | コード    | `node/line.py`       | `send_line`                                     | -              |
| 3-7  | 確定シフトのお知らせの本文を作る       | コード    | `node/line.py`       | `make_final_text`                               | -              |
| 4    | ノードの登録                           | -         | `graph.py`(下のほう) | `builder.add_node`                              | 3              |
| 5    | ノードの連結                           | -         | `graph.py`(下のほう) | `builder.add_edge` など                         | 4              |
| 6    | 実行                                   | -         | `shift_graph.py`     | `if __name__ == "__main__":`                    | 5              |

※ 旧番号は、前の版のコメントや、`データフロー.md` などの古い資料と見比べるための列です。旧「会社の規定に変換」(旧2-12)は、削除したので、番号がありません。 2-2(スキルチェックの分岐)・2-3(スキルチェック)・2-7(手動編成)も、B7 で削除したので、番号がありません(あとで振り直す予定)。

## 3. 流れ

```mermaid
flowchart TD
    S([START]) --> collect
    collect --> auto_draft
    auto_draft[auto_draft<br/>LLMが案を作る] --> validate[validate<br/>コードが検査]
    validate -->|route_2: valid| approve
    validate -->|no_staff 人が足りない日| contact
    validate -->|retry 作り直し| auto_draft
    validate -->|limit_reached / その他| approve
    contact -->|可能| validate
    contact -->|断り・不確か 次の人| contact
    contact -->|尽きた/上限3人| approve
    approve[approve<br/>店長の承認待ち] --> route_3
    route_3 -->|approved かつ問題なし| finalize
    route_3 -->|approved だが資格者なし/重複あり| approve
    route_3 -->|redraft_requested| auto_draft
    route_3 -->|edit_saved| validate
    finalize --> send_final_shift --> E([END])
```

## 4. 分岐は2つ

| 分岐               | どこの次   | 見る値                        | 行き先                                                                                                           |
| ------------------ | ---------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| **route_2** (2-6)  | `validate` | `result`                      | `valid` → `approve` / `no_staff` → `contact` / `retry` → `auto_draft` / `limit_reached`・その他 → `approve` |
| **route_3** (2-10) | `approve`  | `decision`                    | 下の表                                                                                                           |

**route_3 の行き先**(店長の決定ごと):

| decision            | 条件                               | 行き先                                                                         |
| ------------------- | ---------------------------------- | ------------------------------------------------------------------------------ |
| `approved`          | 資格者なし・重複が**残っていない** | `finalize`                                                                     |
| `approved`          | 資格者なし・重複が**残っている**   | `approve` に戻る(理由を `blocked_reasons` に入れる)                            |
| `redraft_requested` | やり直しを頼んだ回数が2回未満      | `auto_draft`(店長のやり直し回数 +1。LLMの回数と、連絡した人数・候補をリセット) |
| `redraft_requested` | すでに2回頼んだ                    | `approve` に戻る(「2回までです」と理由を出す)                                  |
| `edit_saved`        | (店長が案を直して保存した)         | `validate`(LLMは呼ばない)                                                      |
| その他              | -                                  | `ValueError`                                                                   |

## 5. validate の判定(result の決まり方)

`validate` は、上から順に調べて、最初に当てはまったもので `result` が決まります。 問題が直らないときは、問題の一覧つきで、そのまま店長の承認画面に進みます(B7 で、手動編成のノード `manual_draft` はなくしました)。

| 順  | 条件                                                                        | result          | 次(route_2)                                           |
| --- | --------------------------------------------------------------------------- | --------------- | ----------------------------------------------------- |
| ⓪   | 検査回数が上限(`upper_limit`)を超えている                                   | `limit_reached` | `approve`                                        |
| ①   | 案が空                                                                      | `retry`         | `auto_draft`                                          |
| ②   | 問題あり、かつ店長が直した案(`edit_saved`)                                  | `limit_reached` | `approve`(LLMは呼ばない。店長の編集が消えるため) |
| ③   | 問題あり、かつ前回と同じ問題(人数の数字だけ違うものも同じ扱い)              | `limit_reached` | `approve`(これ以上は直らない)                    |
| ④   | 問題あり、かつ作り直しの上限(`UPPER_LIMIT` = 3)に達した                     | `limit_reached` | `approve`                                        |
| ⑤   | 問題あり                                                                    | `retry`         | `auto_draft`(問題の一覧を次のLLMに渡す)               |
| ⑥   | 問題なし                                                                    | `valid`         | `approve`                                             |
| ⑥′  | 問題なしだが、人が足りない日が残っていて、連絡できる人がいる(連絡は3人まで) | `no_staff`      | `contact`                                             |

**検査は7つ**(`node/checks.py`。どれも、問題の文章のリストを返す。空なら問題なし):

| 検査                   | 見ること                                                   | 承認を止める? |
| ---------------------- | ---------------------------------------------------------- | ------------- |
| `check_coverage`       | どの30分にも社員が1人以上いる(開店・閉店作業のため)        | -             |
| `check_qualification`  | 資格者が足りている                                         | **止める**    |
| `check_requests`       | パート・バイトが、申請のない日に入っていない               | -             |
| `check_closing`        | 22:30まで残るのは、`closing_ok` の人だけ                   | -             |
| `check_shain_days`     | 社員の出勤日数が決まりどおり                               | -             |
| `check_headcount`      | 時間帯ごとの最低人数(そもそも足りない日は、ここでは見ない) | -             |
| `check_double_booking` | 同じ人の勤務が、同じ日に重なっていない                     | **止める**    |

「承認を止める」検査は、管理者でも外せません。残っていると `approved` は通らず、`approve` に戻ります。

## 6. 止まる所(interrupt)と、画面・APIが渡す値

グラフは **2か所で止まり**、外から `Command(resume=...)` で再開します。再開すると、その関数は**最初からもう一度走ります**(だから LINE 送信は `interrupt` より前に書かない)。

### ① `approve`(店長の承認待ち)

止まるときに画面へ出す値:

| キー                | 意味                                            |
| ------------------- | ----------------------------------------------- |
| `draft_store_shift` | 店全体の月間シフト案                            |
| `ask`               | `"approved \| redraft_requested \| edit_saved"` |
| `problems`          | 直っていない問題の一覧(参考)                    |
| `blocked_reasons`   | 承認できなかった理由(最大20件。読んだら消える)  |
| `redraft_left`      | あと何回やり直しを頼めるか(上限2回)             |

再開するときに渡す値(文字列だけでも可):

```python
Command(resume="approved")
Command(resume={"decision": "redraft_requested", "instruction": "11/4の9時〜13時を4人以上に"})
Command(resume={"decision": "edit_saved", "assignments": [ {...}, ... ]})   # 店長が直した案
```

### ② `contact`(人が足りない日に、1人ずつ連絡)

- 止まるときの値: `{"ask": "可能 | 断り | 不確か", "staff_id", "staff_name", "date", "start", "end"}`
- 再開するときの値: `"可能"` / `"断り"` / `"不確か"`(`{"answer": "可能"}` でも可。それ以外は `ValueError`)
- 返事ごとの動き:
  | 返事 | 動き |
  |---|---|
  | `可能` | その人の希望と案に、その日の勤務を足す → `validate` に戻る |
  | `断り` / `不確か` | 次の人へ(`不確か` は今回は `断り` と同じ扱い)。上限3人に達するか、候補が尽きたら `approve` |

## 7. 数字のルール(上限)

| もの                | 値                                          | どこ                    |
| ------------------- | ------------------------------------------- | ----------------------- |
| 自動編成(LLM)の回数 | 1回の流れで最大3回(最初の1回 + 作り直し2回) | `UPPER_LIMIT`           |
| 店長の「やり直し」  | 2回まで                                     | `MANAGER_REQUEST_LIMIT` |
| 連絡する人数        | 3人まで                                     | `contact_limit`         |
| `blocked_reasons`   | 画面に最大20件                              | `route_3`               |

店長が「やり直し」を頼むと、LLMの回数は 0 に戻ります(店長のやり直しの回数は、戻りません)。

## 8. State(グラフ全体で共有する箱)

| キー                                                                | 意味                                                                |
| ------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `store_id`                                                          | 店舗ID                                                              |
| `period`                                                            | 対象の月 `{"start": "2026-11-01", "days": 30}`。`period_id` があれば `collect` が DB から入れる。無ければ `settings.PERIOD` |
| `period_id`                                                         | DB の `shift_periods` の ID。あれば `collect` が DB から読む        |
| `store_settings`                                                    | DB から読んだ店の設定。あれば `STORE_SETTINGS[store_id]` より優先   |
| `llm_all_staff`                                                     | 全員のデータ(名前は消してある)                                      |
| `draft_store_shift`                                                 | 店全体の月間シフト案                                                |
| `result`                                                            | `validate` の結果(`valid` / `retry` / `no_staff` / `limit_reached`) |
| `problems`                                                          | 検査で見つかった問題の一覧(作り直しのとき LLM に返す)               |
| `return_count` / `upper_limit`                                      | 自動編成を呼んだ回数 / その上限                                     |
| `decision` / `instruction`                                          | 店長の決定 / 店長の指示                                             |
| `manager_request_count`                                             | 店長が「やり直し」を頼んだ回数                                      |
| `contact_queue` / `contact_asked` / `contact_limit` / `contact_log` | 連絡する候補の順番 / 連絡した人数 / 上限 / 連絡の記録               |
| `blocked_reasons`                                                   | 承認できない理由(承認画面に出す)                                    |
| `final_message`                                                     | 最終シフトの送信メッセージ                                          |

## 9. 動かし方・確かめ方

```powershell
# 本番に近い実行(PostgreSQL が localhost:5433 で動いていて、.env に DATABASE_URL と有効な ANTHROPIC_API_KEY が必要)
cd app\graph
python shift_graph.py start                 # 最初から。承認待ちで止まる
python shift_graph.py start_db period-1 1  # DBから読んで最初から(3つ目が period_id)
python shift_graph.py approve               # 承認で再開
python shift_graph.py contact 可能          # 連絡の返事で再開
python shift_graph.py redraft               # やり直しの依頼で再開

# テスト(ニセのLLMを使うので、DBもAPIキーも不要)
cd test\graph
python test_step1.py                        # 検査・承認・やり直し  T1〜T7・S1〜S2・N1〜N6
python test_contact.py                      # 連絡の流れ            A〜E
python test_convert.py                      # DBの行の変換          (DB不要)
python test_period.py                       # 月の日数・曜日ルール  (DB不要)

# DBを使うテスト(専用の schema を作って、終わったら消す。既存のテーブルは読み書きしない)
$env:TEST_DATABASE_URL = "postgresql://covershift:パスワード@localhost:5433/covershift"
python test_db.py                           # DBから読んで承認まで   D1〜D8(環境変数が無ければ SKIP)
python check_real_db.py                     # 本物のDBを読むだけ(件数だけ表示。名前は出さない)
# test_*.py は、最後に ALL PASS が出れば正常
```

テストは `node.llm.LLM` にニセのLLMを入れて動かします。**本物のLLMは、1回で数分かかります**(11/17 の実測: 17人・30日で、1回目 186秒・2回目 71秒)。

## 10. 今は未完成のところ(正直に)

- `collect` は、State に `period_id` があれば DB から読む(`node/db.py`)。無ければ今までどおり `staff_data.json`。MCP呼び出しはコメントアウト中(D5)。
- `period_id` を渡すと、DB の店舗・月・時間帯ルール・公休日数で動く。渡さないと、`STORE_SETTINGS` の 101 番・`PERIOD`(2026-11、30日)のテスト用データで動く。検査・プロンプトは、月の日数(28〜31日)と、曜日つきルール(`weekdays`: 1=月〜7=日。無ければ毎日)に対応済み。
- **グラフは従業員の名前を読まない**(基準文書 11/07)。`employees.name` は SELECT せず、State にも LLM にも入らない。画面に出る名前は `ID{番号}`。名前に直すのは、画面・API側の役目(まだ未実装)。
- `finalize`(確定の保存)は、メッセージを返すだけの仮実装(B8 は、リーダーの返事待ちで保留)。
- `send_final_shift` は、確定した案で、勤務のある人全員に、1人1通ずつ `node/line.py` の `send_line` で送る(B9)。ただし `send_line` はスタブで、本物は送らない。本物にするときは、`send_line` の中身だけを差し替える。
- `contact` は、LINE送信がなく、返事を受け取る所だけ(B10 で、同じ `node/line.py` の部品を使う予定)。
- `send_final_shift` には、「送り済み」の印がまだ無い。再開すると最初から走り直すので、途中で切れると、同じ人に2回送る恐れがある(B9 の宿題。DB に印の列を作るかどうかは、許可が要る)。
- LINE の ID(`employees.line_user_id`)は State に入れない。本物の `send_line` が、`staff_id` から DB で引く。LINE 未連携の人(NULL)をどうするかは、まだ決めていない。
- DB(v18)には `staff_skill` と `admin_rate` の列がない(DBから読むと両方 `None`)。スキルチェック(`skill_check`・`route_1`)と、手動編成(`manual_draft`)は、B7 で削除した。データの `None` の欄は、まだ残っている。
- 管理者の指示(`manager_actions.rules`)は、まだ `extra_rules` に読み込んでいない(B6)。
- 時間帯ルールの `weekly_days` / `fixed_weekdays` は、まだ使っていない。
- 連絡の候補の順番は、「直近の勤務日数が少ない人が先」だけ(スキルの一致は未実装)。
