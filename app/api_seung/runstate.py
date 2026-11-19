"""RunStatus 를 만드는 곳 (API_CONTRACT 「편성 제어」). 러너가 진짜 상태를 갖고, API 는 thread 가 없을 때만 직접 만든다."""
from __future__ import annotations

import logging

from app.repo import periods as period_repo

log = logging.getLogger("api")


def not_started(period: dict) -> dict:
    return {"period_id": period["period_id"], "thread_id": None, "state": "not_started", "waiting": None,
            "error": None}


def status_of(runner, period: dict) -> dict:
    """thread 가 없으면 러너를 부르지 않고 not_started (F-10: thread 는 지금 시작 때 생긴다)."""
    if period["thread_id"] is None:
        return not_started(period)
    return runner.status(period["period_id"], period["thread_id"])


def run_state(conn, runner, period: dict) -> str:
    """기간 목록 · 상세의 run_state. 확정안이 DB 에 있으면 러너를 부르지 않는다.

    (가정 신규) 러너 호출이 실패해도 기간 목록은 보여야 하므로 그때는 "error" 로 둔다 (원인은 로그).
    """
    if period["thread_id"] is None:
        return "not_started"
    if period_repo.has_confirmed_draft(conn, period["period_id"]):
        return "confirmed"
    try:
        return runner.status(period["period_id"], period["thread_id"]).get("state", "error")
    except Exception as e:  # noqa: BLE001 — 러너의 예외 형은 runtime 에이전트 소유라 여기서 모른다
        log.warning("runner.status 실패 period=%s: %r", period["period_id"], e)
        return "error"
