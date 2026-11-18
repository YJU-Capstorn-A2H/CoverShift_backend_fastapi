"""시연 스크립트 (D-2, D-3): 가짜 그래프의 LocalRunner 로 「승인 대기까지 미리 돌리기 → 재시작 → 상태 확인 → 확정」."""
from __future__ import annotations

import pytest

from scripts import demo_prepare, demo_resume_check
from tests.runtime import dummy_graph
from tests.runtime.conftest import wait_state

APPROVE = {"decision": "approved", "manager_id": 1, "draft_id": 1, "waived_keys": []}


def _period(conn) -> int:
    conn.execute("INSERT INTO manager_accounts(email, password_hash) VALUES ('m@x', 'h')")
    conn.execute("INSERT INTO stores(manager_id, name) VALUES (1, '점포')")
    pid = conn.execute("INSERT INTO shift_periods(store_id, target_month, submission_deadline, designated_off_days) "
                       "VALUES (1, '2026-11-01', '2026-10-20T00:00+09', 9) RETURNING period_id").fetchone()[0]
    conn.commit()
    return pid


@pytest.mark.req("D-2")
def test_prepare_runs_until_waiting_and_is_idempotent(conn, clean_db, make_local):
    pid = _period(conn)
    r = make_local(prepare_delay=0.3)
    lines: list[str] = []
    st = demo_prepare.prepare(pid, r, clean_db, poll=0.05, out=lines.append)
    assert st["state"] == "waiting_review", st
    tid = conn.execute("SELECT thread_id FROM shift_periods WHERE period_id = %s", (pid,)).fetchone()[0]
    assert tid == st["thread_id"] and tid
    assert demo_prepare.summary(st)["draft_id"] == 1
    again = demo_prepare.prepare(pid, r, clean_db, poll=0.05, out=lines.append)  # 다시 불러도 새로 돌리지 않는다
    assert again["thread_id"] == tid and again["waiting"]["draft_id"] == 1
    assert sum("편성 시작" in ln for ln in lines) == 1


@pytest.mark.req("D-3")
def test_resume_check_after_restart_then_confirm(conn, clean_db, make_local):
    pid = _period(conn)
    a = make_local()
    demo_prepare.prepare(pid, a, clean_db, poll=0.05, out=lambda s: None)
    a.close()  # 재시작
    b = make_local()
    st = demo_resume_check.check(pid, b, clean_db)
    assert st["state"] == "waiting_review"
    b.resume(pid, st["thread_id"], APPROVE)
    wait_state(b, pid, st["thread_id"], {"confirmed"})
    assert dummy_graph.commits(st["thread_id"]) == 1


@pytest.mark.req("D-2")
def test_prepare_reports_error_and_unknown_period(conn, clean_db, make_local):
    """깨뜨리는 쪽: 그래프가 실패하면 승인 대기라고 말하지 않는다 (종료 코드도 0 이 아님). 없는 기간은 바로 멈춘다."""
    pid = _period(conn)
    st = demo_prepare.prepare(pid, make_local(fail_in="prepare"), clean_db, poll=0.05, out=lambda s: None)
    assert st["state"] == "error" and demo_prepare.EXIT[st["state"]] != 0
    with pytest.raises(LookupError):
        demo_prepare.prepare(999, make_local(), clean_db, poll=0.05, out=lambda s: None)


@pytest.mark.req("D-2")
def test_prepare_times_out_instead_of_hanging(conn, clean_db, make_local):
    pid = _period(conn)
    st = demo_prepare.prepare(pid, make_local(prepare_delay=2.0), clean_db, timeout=0.3, poll=0.05,
                              out=lambda s: None)
    assert st["state"] == "timeout"
