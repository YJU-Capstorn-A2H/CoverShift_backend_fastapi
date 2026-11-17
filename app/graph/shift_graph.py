# ==============================================================================
# LangGraph/003-Handson/exercises/prototype_mcp.py　(自作LangGraph + MCP のプロトタイプ)
# 
# コードの内容
# ０) 初期設定: 店舗ごとの設定データ、最初に読み込むスタッフデータ、環境変数のロード
# １) 箱作成: Stateクラスの定義
# ２) グラフ作成: グラフのノード(コード・分岐・LLM・待機)を定義する
#                2-1) 【コード】希望・スキル・指示を読む
#                2-2) 【分岐】スキルチェックが必要かどうかを判断する
#                2-3) 【コード】未確認を「要指導」とみなして、店長に知らせる(止まらない)
#                2-4) 【コード】LLMで使う道具
#                2-5) 【LLM】AIが編成案を作る
#                2-6) 【コード】各ルールの検討
#                2-7) 【コード】管理者手動編成
#                2-8) 【コード】人手が足りない日のLINE連絡
#                2-9) 【待機】管理者承認
#               2-10) 【分岐】検査結果に応じて、スタッフに連絡するか、自動編成を再度行うかを判断する
#               2-11) 【コード】最新データで再チェックして、確定を保存
#               2-12) 【コード】最終結果を会社の規定に変換 (現在は、まだ未実装)
#               2-12) 【コード】最終結果を各従業員に通知
# 3) グラフ実行: ノード定義
# 4) グラフの連結: グラフをつなげる
# 5) グラフの実行: グラフを実行する
# ==============================================================================
#
# 【ファイルの分け方 / 파일 구성】
#   settings.py        設定・定数・店舗ごとの設定・スタッフデータ / 설정・상수・점포 설정・스태프 데이터
#   state.py           State の定義 / State 정의
#   routes.py          分岐 route_1〜3 / 분기 route_1〜3
#   graph.py           ノードと、つなぎ(builder) / 노드와 연결(builder)
#   node/checks.py     ルールの検査と計算の道具 / 규칙 검사와 계산 도구
#   node/llm.py        LLM の用意・JSON の取り出し / LLM 준비・JSON 추출
#   node/prompts.py    プロンプトの文章 / 프롬프트 문장
#   shift_graph.py     実行の入口(このファイル) / 실행 입구(이 파일)
# ==============================================================================
import sys, time
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from settings import DB_URL, STORE_SETTINGS     # STORE_SETTINGS は、テストが書き換えるので、ここからも見えるようにしておく
from graph import builder                       # 外から shift_graph.builder で使えるように / 밖에서 shift_graph.builder로 쓸 수 있도록





# ===============================================================================
# 5) グラフ実行
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