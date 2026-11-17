# ==============================================================================
# shift_graph.py -- 実行の入口だけ(ノードの中身は graph.py)
#   python shift_graph.py start / approve / contact 可能 / redraft
#   詳しくは README.md (流れ・止まる所の値・動かし方)
#
# 【処理番号と、ファイル・関数の対応】(番号は、処理の流れの順)
#   0)  初期設定                                            -> settings.py   (定数・STORE_SETTINGS・スタッフデータ)
#   1)  State の箱                                          -> state.py
#   2)  ノード(処理の流れの順)                               -> graph.py / routes.py
#       2-1)  【コード】希望・スキル・指示を読む               -> graph.py: collect
#       2-2)  【分岐】スキルチェックが必要か                  -> routes.py: route_1
#       2-3)  【コード】未確認を店長に知らせる(止まらない)      -> graph.py: skill_check
#       2-4)  【LLM】AIが編成案を作る                        -> graph.py: auto_draft
#       2-5)  【コード】各ルールの検査                        -> graph.py: validate
#       2-6)  【分岐】検査の結果で、次の行き先を決める         -> routes.py: route_2
#       2-7)  【コード】管理者の手動編成                      -> graph.py: manual_draft
#       2-8)  【待機】人手が足りない日の連絡(LINE送信は未実装) -> graph.py: contact
#       2-9)  【待機】管理者の承認                           -> graph.py: approve
#       2-10) 【分岐】店長の決定で、次の行き先を決める         -> routes.py: route_3
#       2-11) 【コード】確定を保存(仮実装)                    -> graph.py: finalize
#       2-12) 【コード】各従業員に通知(仮実装)                -> graph.py: send_final_shift
#   3)  ノードが使う道具
#       3-1)  検査と計算                                    -> node/checks.py
#       3-2)  LLM の用意・JSON の取り出し                    -> node/llm.py
#       3-3)  LLM に渡す文章                                -> node/prompts.py
#   4)  グラフの設計: ノードを登録する                        -> graph.py の下のほう(builder)
#   5)  グラフの連結: ノードをつなぐ                          -> graph.py の下のほう(builder)
#   6)  グラフの実行                                        -> shift_graph.py (このファイル)
# ==============================================================================
import sys, time
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from settings import DB_URL, STORE_SETTINGS     # STORE_SETTINGS は、テストが書き換えるので、ここからも見えるようにしておく
from graph import builder                       # 外から shift_graph.builder で使えるように





# ===============================================================================
# 6) グラフ実行
# ===============================================================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"            # 実行モードを取得する（デフォルトは "start"）
    thread = sys.argv[2] if len(sys.argv) > 2 else "test_resume_1"  # スレッドIDを取得する（デフォルトは "test_resume_1"）

    with PostgresSaver.from_conn_string(DB_URL) as saver:    # データベースに接続する
        saver.setup()                                        # データベースのテーブルを作成する
        graph = builder.compile(checkpointer=saver)          # グラフをコンパイルする
        config = {"configurable": {"thread_id": thread}}     # スレッドIDを設定する
        start = time.time()                                  # 開始時間を記録する
    
        if mode == "start":
            inp = {"store_id": 101}
        elif mode == "approve":
            inp = Command(resume="approved")
        elif mode == "contact":                              # 連絡の返事で再開する (例: contact 可能)
            inp = Command(resume=sys.argv[3] if len(sys.argv) > 3 else "可能")
        elif mode == "redraft":
            inp = Command(resume={"decision": "redraft_requested", "instruction": "11/4の9時〜13時を4人以上にしてください"})
        else:
            raise SystemExit(f"不明なモード: {mode} (start / approve / contact / redraft)")

        for chunk in graph.stream(inp, config, stream_mode="updates"):                  # グラフをストリームモードで実行する
            for node_name, update in chunk.items():                                     # ノード名と更新内容を表示する
                print(f"--- {node_name} が終わりました（{time.time() - start:.1f}秒）")   # 経過時間を表示する

        print("ストリーム終了")
        print("次のノード:", graph.get_state(config).next)