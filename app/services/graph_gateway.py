# app/services/graph_gateway.py
# APIとグラフの「つなぎ目」。ここだけを差し替えれば、Aegra・直接実行のどちらにもつながる。
# API와 그래프의 「이음매」. 여기만 교체하면 Aegra・직접 실행 어느 쪽에도 연결된다.
#
# グラフ側の約束（shift_graph.py の approve / route_3 と同じ）:
# 그래프 측 약속(shift_graph.py의 approve / route_3과 같다):
#   resume の値 = "approved"
#               | {"decision": "redraft_requested", "instruction": "..."}
#               | {"decision": "edit_saved", "assignments": [{"staff_id","date","start","end"}, ...]}
from typing import Protocol

from app.core.errors import ApiError


class GraphGateway(Protocol):
    def start(self, thread_id: str, store_id: int, period_id: int) -> None:
        """編成グラフを開始する（承認待ちで止まるまで待たなくてよい）。
        편성 그래프를 시작한다(승인 대기에서 멈출 때까지 기다리지 않아도 된다)."""

    def resume(self, thread_id: str, value) -> dict:
        """止まっているグラフを続きから動かす。
        戻り値: {"next": [...], "result": str | None, "problems": [str, ...]}
        멈춰 있는 그래프를 이어서 동작시킨다.
        반환값: {"next": [...], "result": str | None, "problems": [str, ...]}"""


class NotConnectedGateway:
    # 既定: まだつないでいない（D3: スンヒョクの入口API / Aegra）。
    # 기본: 아직 연결하지 않음(D3: 승혁의 입구 API / Aegra).
    def start(self, thread_id: str, store_id: int, period_id: int) -> None:
        raise ApiError(503, "GRAPH_NOT_CONNECTED", "グラフ実行側が未接続です / 그래프 실행 측이 연결되지 않았습니다")

    def resume(self, thread_id: str, value) -> dict:
        raise ApiError(503, "GRAPH_NOT_CONNECTED", "グラフ実行側が未接続です / 그래프 실행 측이 연결되지 않았습니다")


_gateway: GraphGateway = NotConnectedGateway()


def set_gateway(gw: GraphGateway) -> None:
    # 起動時に1回だけ差し替える / 기동 시 한 번만 교체한다
    global _gateway
    _gateway = gw


def get_gateway() -> GraphGateway:
    # FastAPI の Depends で使う（テストでは dependency_overrides で偽物に差し替える）
    # FastAPI의 Depends로 쓴다(테스트에서는 dependency_overrides로 가짜로 교체한다)
    return _gateway
