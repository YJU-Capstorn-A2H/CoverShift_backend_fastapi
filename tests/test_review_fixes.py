"""리뷰어 지적을 고친 것의 회귀 시험 (메인 세션 소유)."""
import pytest

from app.graph_seung.nodes import _merge_impossible


@pytest.mark.req("F-14")
def test_impossible_slots_become_contract_shape_and_merge():
    """리뷰 B M2: 30분마다 1줄 · have 없음 → 계약 모양 + 이어지는 칸은 한 줄."""
    rows = [
        {"work_date": "2026-11-02", "time": t, "start_time": t, "end_time": e, "need": 4, "max_possible": 3,
         "severity": "warning", "source": "setting", "rule_key": None, "reason": "theoretical_max_below_need"}
        for t, e in (("09:00", "09:30"), ("09:30", "10:00"), ("10:00", "10:30"))
    ] + [  # 깨뜨리는 쪽: 최대 인원이 다르면 합치지 않는다 · 끊긴 칸도 합치지 않는다
        {"work_date": "2026-11-02", "start_time": "10:30", "end_time": "11:00", "need": 4, "max_possible": 2},
        {"work_date": "2026-11-02", "start_time": "12:00", "end_time": "12:30", "need": 4, "max_possible": 2},
    ]
    out = _merge_impossible(rows)
    assert out == [
        {"work_date": "2026-11-02", "start_time": "09:00", "end_time": "10:30", "need": 4, "have": 3, "reason": "impossible"},
        {"work_date": "2026-11-02", "start_time": "10:30", "end_time": "11:00", "need": 4, "have": 2, "reason": "impossible"},
        {"work_date": "2026-11-02", "start_time": "12:00", "end_time": "12:30", "need": 4, "have": 2, "reason": "impossible"},
    ]
    assert all(set(r) == {"work_date", "start_time", "end_time", "need", "have", "reason"} for r in out)


# ---------------------------------------------------------------- 리뷰 A #5: 실명을 LLM 에 보내지 않는다

from app.privacy import AMBIGUOUS, mask_names  # noqa: E402


@pytest.mark.req("F-6")
def test_mask_names_full_name_parts_and_shared_surname():
    emps = [(1, "渡辺 花子"), (2, "伊藤 健"), (3, "渡辺 翔"), (4, "Aさん")]
    out = mask_names("渡辺 花子さんと伊藤さんは同じ時間に入れないで。渡辺さんは日曜休み。Aさんも。", emps)
    assert out == f"ID1さんとID2さんは同じ時間に入れないで。{AMBIGUOUS}さんは日曜休み。ID4も。"
    # 깨뜨리는 쪽: 이름이 없는 문장은 그대로
    assert mask_names("金曜の夜は3人ほしい", emps) == "金曜の夜は3人ほしい"


@pytest.fixture
def api_seeded(clean_db, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "review-fix-only-0123456789abcdefghij")
    monkeypatch.setenv("LLM_MODE", "fake")
    monkeypatch.setenv("RUNNER", "local")
    import psycopg
    from psycopg.rows import dict_row
    from fastapi.testclient import TestClient

    from app.api_seung.main import app
    from app.seed import seed_db

    with psycopg.connect(clean_db, row_factory=dict_row) as c:
        seed_db(c, manager_email="fix@example.test", password="fix-password-1")
        name1 = c.execute("SELECT name FROM employees WHERE employee_id = 1").fetchone()["name"]
        c.commit()
    with TestClient(app) as cl:
        tok = cl.post("/api/auth/manager/login",
                      json={"email": "fix@example.test", "password": "fix-password-1"}).json()["access_token"]
        yield cl, {"Authorization": f"Bearer {tok}"}, name1, clean_db


@pytest.mark.req("F-6", "M-2")
def test_instruction_preview_and_confirm_send_ids_not_names(api_seeded):
    cl, h, name1, url = api_seeded
    pid = cl.get("/api/shift-periods", headers=h).json()[0]["period_id"]
    text = f"{name1}は新人なので、慣れている人と一緒に入れてあげて"  # 틀에 안 맞는 말 → free_text
    r = cl.post(f"/api/shift-periods/{pid}/instructions/preview", headers=h, json={"text": text})
    assert r.status_code == 200, r.text
    assert name1 not in r.text and "ID1" in r.text
    # 화면이 원문 free_text 를 그대로 다시 보내도, 저장(=그래프가 LLM 에 넘기는 값)은 ID 로 바뀐다
    r = cl.post(f"/api/shift-periods/{pid}/instructions/confirm", headers=h,
                json={"text": text, "rules": [], "free_text": [text]})
    assert r.status_code == 201, r.text
    import psycopg
    with psycopg.connect(url) as c:
        stored = c.execute("SELECT rules FROM manager_actions WHERE action = 'rules_confirmed'").fetchone()[0]
    assert name1 not in str(stored["free_text"]) and "ID1" in stored["free_text"][0]


# ---------------------------------------------------------------- 리뷰 A #17: MCP 가 이름을 못 보는 롤을 실제로 쓰는가

@pytest.mark.req("A-18", "M-7")
def test_get_staff_info_really_runs_as_restricted_role(conn):
    """SET ROLE 을 지워도 출력만 보는 시험은 통과했다 (리뷰 A 변이 M6). 롤의 권한을 잠깐 빼서, 롤로 읽는지 직접 본다."""
    import psycopg

    from app.mcp_tools import get_staff_info

    conn.execute("insert into manager_accounts(email,password_hash) values ('a@x','h')")
    conn.execute("insert into stores(manager_id,name) values (1,'s')")
    conn.execute("insert into employees(store_id,name,is_regular) values (1,'실명',true)")
    conn.execute("insert into shift_periods(store_id,target_month,submission_deadline,designated_off_days) "
                 "values (1,'2026-11-01','2026-10-20T00:00+09',9)")
    conn.execute("REVOKE SELECT (closing_ok) ON employees FROM covershift_llm_read")
    conn.commit()
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            get_staff_info(1)
    finally:
        conn.rollback()
        conn.execute("GRANT SELECT (closing_ok) ON employees TO covershift_llm_read")
        conn.commit()
    assert get_staff_info(1)["employees"][0]["closing_ok"] is False


# ---------------------------------------------------------------- 리뷰 A #14 · D2: 멈춘 기간 되살리기

@pytest.mark.req("D-3")
def test_reset_thread_refuses_confirmed_and_waiting_and_clears_stalled(conn):
    from ops.reset_thread import plan, reset
    from psycopg.rows import dict_row

    conn.row_factory = dict_row
    conn.execute("insert into manager_accounts(email,password_hash) values ('a@x','h')")
    conn.execute("insert into stores(manager_id,name) values (1,'s')")
    for m, tid in (("2026-11-01", "period-1-aaaa0001"), ("2026-12-01", "period-2-bbbb0002"), ("2027-01-01", None)):
        conn.execute("insert into shift_periods(store_id,target_month,submission_deadline,designated_off_days,thread_id) "
                     "values (1,%s,'2026-10-20T00:00+09',9,%s)", (m, tid))
    conn.execute("insert into shift_drafts(period_id,status,confirmed_at,confirmed_by) values (1,'confirmed',now(),1)")
    # 깨뜨리는 쪽: 확정안 있음 · 대기 중 · 시작 전 · 없는 기간은 하지 않는다
    assert plan(conn, 1, "error")[0] is False
    assert plan(conn, 2, "waiting_review")[0] is False
    assert plan(conn, 2, "running")[0] is False
    assert plan(conn, 3, "error")[0] is False
    assert plan(conn, 99, "error")[0] is False
    # 멈춘(error) 기간은 떼어 낸다
    assert plan(conn, 2, "error")[0] is True
    assert plan(conn, 2, "waiting_review", force=True)[0] is True
    reset(conn, 2)
    assert conn.execute("select thread_id from shift_periods where period_id=2").fetchone()["thread_id"] is None
    assert conn.execute("select thread_id from shift_periods where period_id=1").fetchone()["thread_id"] == "period-1-aaaa0001"
