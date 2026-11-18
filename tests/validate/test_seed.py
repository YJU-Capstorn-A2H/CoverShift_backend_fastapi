"""app/seed.py (가정 G20): 시험 DB(fixture conn) 에 넣고, DB 에서 다시 만든 문맥이 load_azuki_context 와 같은지."""
from __future__ import annotations

import io
import json
from collections import Counter
from datetime import datetime, time, timezone

import bcrypt
import psycopg
import pytest

from app.domain import Period, PeriodContext, TimeRule
from app.mcp_tools import get_staff_info, staff_info_to_domain
from app.seed import load_azuki_context, main, seed_db
from app.validate import validate_draft

PW = "seed-test-only"  # 시험 DB 전용 값 (시크릿 아님)


def _context_from_db(c, period_id: int) -> PeriodContext:
    """DB → PeriodContext. 직원・희망은 MCP 읽기 도구(이름 못 읽는 롤)로, 시간대 규칙・기간은 직접 읽는다."""
    emps, av = staff_info_to_domain(get_staff_info(period_id))
    p = c.execute("SELECT period_id, store_id, target_month, designated_off_days FROM shift_periods WHERE period_id = %s",
                  (period_id,)).fetchone()
    rules = c.execute("""SELECT rule_id, weekday, start_time, end_time, min_headcount, min_qualified
                         FROM store_time_rules WHERE store_id = %s ORDER BY weekday, start_time""", (p[1],)).fetchall()
    return PeriodContext(Period(p[0], p[1], p[2], p[3]), emps, av, tuple(TimeRule(*r) for r in rules))


@pytest.mark.req("M-1", "F-16")
def test_seed_db_inserts_everything_with_valid_values(conn):
    ids = seed_db(conn, manager_email="demo@example.com", password=PW)
    conn.commit()
    assert len(ids["employee_ids"]) == 17 and ids["availability_count"] == 286 and len(ids["time_rule_ids"]) == 21
    assert ids["employee_ids"] == {i: i for i in range(1, 18)}  # 빈 DB + staff_id 순서 → 같은 번호
    h, active = conn.execute("SELECT password_hash, is_active FROM manager_accounts WHERE manager_id = %s",
                             (ids["manager_id"],)).fetchone()
    assert active and h != PW and bcrypt.checkpw(PW.encode(), h.encode())
    kinds = dict(conn.execute("SELECT kind, count(*) FROM availability_submissions GROUP BY kind").fetchall())
    assert kinds == {"available": 253, "off": 20, "paid_leave": 7, "half_leave": 6} and sum(kinds.values()) == 286
    # 시각은 available・half_leave 에만 (DDL CHECK) — 넣을 때 이미 DB 가 확인했지만 한 번 더
    assert conn.execute("SELECT count(*) FROM availability_submissions WHERE (kind IN ('off','paid_leave')) "
                        "<> (start_time IS NULL)").fetchone()[0] == 0
    per = conn.execute("SELECT target_month, submission_deadline, designated_off_days FROM shift_periods").fetchone()
    assert per[0].isoformat() == "2026-11-01" and per[2] == 9
    assert per[1] == datetime(2026, 10, 25, 14, 59, tzinfo=timezone.utc)  # 23:59+09:00
    names = [r[0] for r in conn.execute("SELECT name FROM employees ORDER BY employee_id").fetchall()]
    assert names[0] == "Aさん" and len(names) == 17
    reg = conn.execute("SELECT count(*), bool_and(closing_ok) FROM employees WHERE is_regular").fetchone()
    assert reg == (5, True)  # (가정 신규) 사원은 closing_ok
    qual = sorted(r[0] for r in conn.execute("SELECT employee_id FROM employees WHERE has_qualification").fetchall())
    assert qual == [1, 2, 3, 5, 16, 17]  # 登録販売者(管理者) + 薬剤師. 研修中(4)는 아님
    bands = conn.execute("SELECT DISTINCT start_time, end_time, min_headcount, min_qualified FROM store_time_rules "
                         "ORDER BY start_time").fetchall()
    assert bands == [(time(8), time(9), 3, 1), (time(9), time(13), 4, 1), (time(13), time(22, 30), 1, 1)]


@pytest.mark.req("M-1", "A-18")
def test_context_rebuilt_from_seeded_db_agrees_with_load_azuki_context(conn):
    ids = seed_db(conn, manager_email="demo@example.com", password=PW)
    conn.commit()  # get_staff_info 는 다른 연결로 읽는다
    db_ctx = _context_from_db(conn, ids["period_id"])
    js_ctx = load_azuki_context()
    assert len(db_ctx.employees) == len(js_ctx.employees) == 17
    assert len(db_ctx.availability) == len(js_ctx.availability) == 286
    assert len(db_ctx.time_rules) == len(js_ctx.time_rules) == 21
    to_db = ids["employee_ids"]
    assert {(to_db[e.employee_id], e.is_regular, e.has_qualification, e.closing_ok) for e in js_ctx.employees} == \
           {(e.employee_id, e.is_regular, e.has_qualification, e.closing_ok) for e in db_ctx.employees}
    assert Counter((to_db[a.employee_id], a.work_date, a.kind, a.start_time, a.end_time) for a in js_ctx.availability) == \
           Counter((a.employee_id, a.work_date, a.kind, a.start_time, a.end_time) for a in db_ctx.availability)
    # 같은 빈 안을 두 문맥으로 검사하면 같은 위반 수
    key = lambda vs: Counter((v.check_code, v.severity) for v in vs)  # noqa: E731
    assert key(validate_draft(db_ctx, [])) == key(validate_draft(js_ctx, []))


@pytest.mark.req("M-1")
def test_seed_db_is_atomic_on_failure(conn):
    seed_db(conn, manager_email="demo@example.com", password=PW)
    conn.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):  # 같은 email → 첫 INSERT 에서 실패
        seed_db(conn, manager_email="demo@example.com", password=PW)
    conn.rollback()
    assert conn.execute("SELECT count(*) FROM employees").fetchone()[0] == 17
    assert conn.execute("SELECT count(*) FROM stores").fetchone()[0] == 1


@pytest.mark.req("M-1")
def test_seed_db_rejects_empty_password(conn):
    with pytest.raises(ValueError):
        seed_db(conn, manager_email="demo@example.com", password="")


@pytest.mark.req("M-1")
def test_cli_reads_password_from_stdin_and_does_not_print_it(clean_db, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(PW + "\n"))
    assert main(["--email", "cli@example.com", "--password-stdin"]) == 0
    out = capsys.readouterr().out
    assert PW not in out and clean_db not in out
    printed = json.loads(out)
    assert printed["availability_count"] == 286 and len(printed["employee_ids"]) == 17
    with psycopg.connect(clean_db) as c:
        h = c.execute("SELECT password_hash FROM manager_accounts WHERE email = 'cli@example.com'").fetchone()[0]
    assert bcrypt.checkpw(PW.encode(), h.encode())


@pytest.mark.req("M-1")
def test_cli_refuses_password_on_command_line(clean_db, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as e:
        main(["--email", "cli@example.com"])
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        main(["--email", "cli@example.com", "--password-stdin"])  # 빈 표준 입력
