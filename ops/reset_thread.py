"""멈춘 편성 기간을 처음부터 다시 시작할 수 있게 thread 를 떼어 낸다 (리뷰 A #14 · D2). 운영 스크립트 — 사람이 실행한다.

언제: 편성 도중 프로세스가 죽어 run-status 가 error(「중간에 멈췄습니다」)이거나, Aegra 가 run 도중 SIGKILL 로 죽어
     그 thread 가 계속 BUSY 일 때. 그 기간은 「지금 시작」(409 ALREADY_STARTED)도 review 도 받지 않는다.
무엇을: shift_periods.thread_id 를 NULL 로 만든다. 옛 체크포인트 · 확정 안 된 편성안 · 위반 기록은 지우지 않고 남긴다(기록).
       다음 「지금 시작」은 새 thread(period-{id}-<임의>, G22)로 처음부터 돈다 → LLM 을 다시 부른다(비용).
막는 것: 확정안이 있는 기간(되돌리면 확정표와 어긋남) · 승인/연락 대기 중인 기간(멈춘 것이 아님, --force 로만) ·
       지금 돌고 있는 기간(--force 로만).

  uv run python -m ops.reset_thread --period-id 3            # 무엇을 할지 보여 주기만 (DB 를 바꾸지 않음)
  uv run python -m ops.reset_thread --period-id 3 --yes      # 실행
"""
from __future__ import annotations

import argparse
import sys

from app.db import connect

REFUSE_UNLESS_FORCE = {"running", "waiting_review", "waiting_contact"}


def plan(conn, period_id: int, state: str, *, force: bool = False) -> tuple[bool, str]:
    """(해도 되는가, 이유). DB 를 바꾸지 않는다."""
    p = conn.execute("SELECT period_id, thread_id FROM shift_periods WHERE period_id = %s", (period_id,)).fetchone()
    if p is None:
        return False, f"기간 {period_id} 없음"
    if conn.execute("SELECT 1 FROM shift_drafts WHERE period_id = %s AND status = 'confirmed'", (period_id,)).fetchone():
        return False, "확정안이 있는 기간은 되돌리지 않는다"
    if p["thread_id"] is None:
        return False, "thread 가 없다 (아직 시작 전) — 그냥 「지금 시작」하면 된다"
    if state in REFUSE_UNLESS_FORCE and not force:
        return False, f"지금 상태가 {state} 다 — 멈춘 것이 아니다. 정말 버리려면 --force"
    return True, f"thread {p['thread_id']} (상태 {state}) 를 떼어 낸다. 다음 「지금 시작」은 새 thread 로 처음부터"


def reset(conn, period_id: int) -> None:
    conn.execute("UPDATE shift_periods SET thread_id = NULL WHERE period_id = %s", (period_id,))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--period-id", type=int, required=True)
    ap.add_argument("--yes", action="store_true", help="실제로 바꾼다 (없으면 보여 주기만)")
    ap.add_argument("--force", action="store_true", help="돌고 있거나 대기 중이어도 떼어 낸다")
    a = ap.parse_args(argv)
    from app.runner import get_runner

    with connect() as conn:
        p = conn.execute("SELECT thread_id FROM shift_periods WHERE period_id = %s", (a.period_id,)).fetchone()
        try:
            state = get_runner().status(a.period_id, p["thread_id"] if p else None).get("state", "error")
        except Exception as e:  # noqa: BLE001 — 러너에 닿지 못해도 판단은 할 수 있게 (상태를 모름으로 둔다)
            state = f"unknown({e.__class__.__name__})"
        ok, why = plan(conn, a.period_id, state, force=a.force)
        print(("할 수 있음: " if ok else "하지 않음: ") + why)
        if not ok:
            return 2
        if not a.yes:
            print("(보여 주기만 했다. 실행하려면 --yes)")
            return 0
        reset(conn, a.period_id)
        conn.commit()
        print("thread_id 를 비웠다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
