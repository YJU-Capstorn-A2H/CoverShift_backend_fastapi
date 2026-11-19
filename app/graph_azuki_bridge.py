# app/graph_azuki_bridge.py -- アズキのグラフ(app/graph/)を、runner の入口 build_graph(checkpointer, deps) の形で渡す橋
#   ・app/graph/ のファイルは、1文字も変えない(あのフォルダは「フォルダの中から動かす」作りで、from settings import ... と書いてある)
#   ・ここでは、そのフォルダを import の探す場所に足して、builder を取り出し、checkpointer をつけてコンパイルするだけ
#   ・使い方: 環境変数 GRAPH_IMPL=azuki で、runner がこの橋を使う(既定は 승혁 の app/graph_seung)
#   ・まだ無いもの(接続の宿題): 止まる所の値の形の変換 / 下書きを DB に保存 / 違反の形の変換 / 偽LLM — メモ「接続仕様」を参照
#   (추가 · 11/19) 아즈키 그래프를 runner 계약의 build_graph 모양으로 건네는 다리.
import sys
from pathlib import Path

_GRAPH_DIR = str(Path(__file__).resolve().parent / "graph")


def build_graph(checkpointer=None, deps=None):
    """コンパイル済みのグラフを返す。deps は今は使わない(승혁 のグラフ用の引数。形を合わせるためだけに受ける)。"""
    if _GRAPH_DIR not in sys.path:
        sys.path.append(_GRAPH_DIR)  # 末尾に足す: settings / state / routes / node などの名前が、他の物を隠さないように
    from graph import builder  # app/graph/graph.py の builder (StateGraph)
    return builder.compile(checkpointer=checkpointer)
