"""시연 데이터 (가정 G20): 아즈키 staff_data.json(11/04, 2026-11 한 달) + 시제품 STORE_SETTINGS[101].

- load_azuki_context(): DB 없이 PeriodContext 를 만든다 (ID = 아즈키 staff_id). 검증 테스트용.
- seed_db(): 같은 내용을 DB 에 넣는다 (db/001 의 CHECK 를 전부 지키는 값으로). 이름은 원본의 더미 이름(Aさん 등).
- CLI: `uv run python -m app.seed --email demo@example.com --password-stdin`  (DATABASE_URL 은 app.config)
  비밀번호는 표준 입력으로만 받는다 (명령줄 인자로 받으면 셸 기록에 남는다). 출력에 비밀번호・접속 문자열을 쓰지 않는다.

시제품(spec/azuki_prototype_mcp_1107.py 52-73)에서 옮긴 값:
- 기간 2026-11-01 부터 30일, 사원 공휴일수 9 (shain_off_days).
- 근무 시간 08:00~22:30 = 영업 09:00~22:00 + 준비 60분 + 마감 30분 (prep_before · close_after). V9_ko 468 의 08:00~22:30 과 같다.
- 최소 인원: 08:00~09:00 3명, 09:00~13:00 4명 (want 5).
- 자격자: 「登録販売者(管理者)」「薬剤師」 08:00~22:30 1명 이상 → 모든 시간대 min_qualified = 1.
옮기지 못한 것 (db/001 표에 열이 없음):
- 08:00~09:00 의 exclude ["薬剤師"] (그 시간대 인원에 약사를 세지 않음) → 세게 된다.
- 09:00~13:00 의 want 5 → 余り(V-10)의 want 는 G3 대로 min_headcount(4)를 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain import Availability, Employee, Period, PeriodContext, TimeRule, ValidateOptions

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "seed" / "azuki_staff_data_1104.json"

TARGET_MONTH = date(2026, 11, 1)
DESIGNATED_OFF_DAYS = 9  # 시제품 STORE_SETTINGS[101]["shain_off_days"]
SUBMISSION_DEADLINE = datetime(2026, 10, 25, 23, 59, tzinfo=ZoneInfo("Asia/Tokyo"))
STORE_NAME = "デモ店舗101"
QUALIFIED = frozenset({"登録販売者(管理者)", "薬剤師"})  # 시제품 qualification_rules. 「登録販売者(研修中)」은 자격자가 아니다

# (시작, 종료, min_headcount, min_qualified). 요일마다 같다 (시제품 open_by_weekday = {}).
# (가정 신규) 13:00~22:30 은 시제품에 최소 인원 규칙이 없다. 그러나 G6 은 영업시간을 store_time_rules 에서 읽고
# DB 는 min_headcount >= 1 을 요구한다(V9_ko 240) → 「규칙 없음」에 가장 가까운 1 로 넣는다. 22:30 까지 영업시간이 되게 하려는 행이다.
TIME_BANDS: tuple[tuple[str, str, int, int], ...] = (
    ("08:00", "09:00", 3, 1),
    ("09:00", "13:00", 4, 1),
    ("13:00", "22:30", 1, 1),
)


def _load_json(path: str | Path | None) -> list[dict]:
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        return json.load(f)


def _employee_fields(s: dict) -> dict:
    is_regular = s["employment"] == "社員"
    return {
        "is_regular": is_regular,
        "has_qualification": s.get("qualification") in QUALIFIED,
        # (가정 신규) 사원은 closing_ok=true. 원본 JSON 의 사원에는 closing_ok 키가 없다.
        # 시제품 프롬프트 규칙 1(「開店作業と閉店作業は、この社員が行う」 시제품 216)이 사원의 마감을 전제하고,
        # 검증 V-4 를 사원에게도 적용하므로(app/validate/rules.py) false 로 두면 V-1(사원 1명 이상)과 22:00~22:30 칸에서 서로 막힌다.
        "closing_ok": bool(s.get("closing_ok")) or is_regular,
    }


def _availability_fields(r: dict) -> dict:
    """요청 1건 → availability_submissions 의 (kind, start, end). DDL CHECK(V9_ko 313-316)에 맞춘다:
    available · half_leave 만 시각이 있고, 시각은 30분 단위, end > start."""
    if r.get("off"):
        return {"kind": "off", "start_time": None, "end_time": None}
    if r.get("paid_leave") is True:
        return {"kind": "paid_leave", "start_time": None, "end_time": None}
    s, e = time.fromisoformat(r["start"]), time.fromisoformat(r["end"])
    if r.get("paid_leave") == "half":
        return {"kind": "half_leave", "start_time": s, "end_time": e}
    return {"kind": "available", "start_time": s, "end_time": e}


def _time_rules(rule_id_start: int = 1) -> tuple[TimeRule, ...]:
    out: list[TimeRule] = []
    rid = rule_id_start
    for wd in range(1, 8):
        for s, e, mh, mq in TIME_BANDS:
            out.append(TimeRule(rid, wd, time.fromisoformat(s), time.fromisoformat(e), mh, mq))
            rid += 1
    return tuple(out)


def load_azuki_context(path: str | Path | None = None, options: ValidateOptions = ValidateOptions()) -> PeriodContext:
    """DB 없이 만든 시연 PeriodContext. employee_id = 아즈키 staff_id, period_id = store_id = 1."""
    staff = _load_json(path)
    emps = tuple(Employee(employee_id=s["staff_id"], **_employee_fields(s)) for s in sorted(staff, key=lambda s: s["staff_id"]))
    av = tuple(Availability(employee_id=s["staff_id"], work_date=date.fromisoformat(r["date"]), **_availability_fields(r))
               for s in sorted(staff, key=lambda s: s["staff_id"]) for r in s.get("requests", []))
    period = Period(period_id=1, store_id=1, target_month=TARGET_MONTH, designated_off_days=DESIGNATED_OFF_DAYS)
    return PeriodContext(period=period, employees=emps, availability=av, time_rules=_time_rules(), options=options)


def seed_db(conn, path: str | Path | None = None, *, manager_email: str, password: str) -> dict:
    """시연 데이터를 DB 에 넣고 만든 ID 를 돌려준다.

    - 한 묶음으로 넣는다 (conn.transaction()). 실패하면 아무것도 남지 않는다.
      연결이 쉬는 중(IDLE)이면 블록이 끝날 때 커밋된다. 바깥 트랜잭션이 열려 있으면(INTRANS) savepoint 가 되어 커밋은 부른 쪽이 한다.
      (psycopg 3.3.6 에서 확인: 쉬는 연결 → 블록 뒤 IDLE / 열린 트랜잭션 → 블록 뒤 INTRANS)
    - 직원은 staff_id 순서로 넣는다 → 빈 DB(IDENTITY 1부터)라면 employee_id = staff_id. 그래도 대응표 employee_ids 를 돌려준다.
    - 비밀번호는 bcrypt 해시만 저장한다.
    """
    import bcrypt
    from psycopg.rows import tuple_row

    if not password:
        raise ValueError("password 가 비었다")
    staff = sorted(_load_json(path), key=lambda s: s["staff_id"])
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    with conn.transaction():
        cur = conn.cursor(row_factory=tuple_row)  # 부른 쪽 연결이 dict_row 여도 같은 코드로 읽는다
        manager_id = cur.execute("INSERT INTO manager_accounts(email, password_hash) VALUES (%s, %s) RETURNING manager_id",
                                 (manager_email, pw_hash)).fetchone()[0]
        store_id = cur.execute("INSERT INTO stores(manager_id, name) VALUES (%s, %s) RETURNING store_id",
                               (manager_id, STORE_NAME)).fetchone()[0]
        rule_ids: list[int] = []
        for r in _time_rules():
            rule_ids.append(cur.execute(
                """INSERT INTO store_time_rules(store_id, weekday, start_time, end_time, min_headcount, min_qualified)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING rule_id""",
                (store_id, r.weekday, r.start_time, r.end_time, r.min_headcount, r.min_qualified)).fetchone()[0])
        emp_ids: dict[int, int] = {}
        for s in staff:
            f = _employee_fields(s)
            emp_ids[s["staff_id"]] = cur.execute(
                """INSERT INTO employees(store_id, name, is_regular, has_qualification, closing_ok)
                   VALUES (%s, %s, %s, %s, %s) RETURNING employee_id""",
                (store_id, s["staff_name"], f["is_regular"], f["has_qualification"], f["closing_ok"])).fetchone()[0]
        period_id = cur.execute(
            """INSERT INTO shift_periods(store_id, target_month, submission_deadline, designated_off_days)
               VALUES (%s, %s, %s, %s) RETURNING period_id""",
            (store_id, TARGET_MONTH, SUBMISSION_DEADLINE, DESIGNATED_OFF_DAYS)).fetchone()[0]
        n_av = 0
        for s in staff:
            for r in s.get("requests", []):
                a = _availability_fields(r)
                cur.execute(
                    """INSERT INTO availability_submissions(employee_id, work_date, kind, start_time, end_time)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (emp_ids[s["staff_id"]], date.fromisoformat(r["date"]), a["kind"], a["start_time"], a["end_time"]))
                n_av += 1
    return {"manager_id": manager_id, "store_id": store_id, "period_id": period_id,
            "time_rule_ids": rule_ids, "employee_ids": emp_ids, "availability_count": n_av}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.seed", description="시연 데이터(아즈키 2026-11)를 DATABASE_URL 의 DB 에 넣는다")
    ap.add_argument("--email", required=True, help="만들 점장 계정의 email")
    ap.add_argument("--password-stdin", action="store_true", help="비밀번호를 표준 입력 첫 줄에서 읽는다 (필수)")
    ap.add_argument("--path", default=None, help=f"staff JSON (기본 {DEFAULT_PATH.relative_to(ROOT)})")
    args = ap.parse_args(argv)
    if not args.password_stdin:
        ap.error("비밀번호는 --password-stdin 으로만 받는다 (명령줄에 쓰지 않는다)")
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        ap.error("표준 입력에서 비밀번호를 읽지 못했다 (빈 줄)")

    from app.db import connect

    with connect() as c:  # DATABASE_URL (app.config). 블록을 나오면 커밋된다
        ids = seed_db(c, args.path, manager_email=args.email, password=password)
    print(json.dumps({**ids, "employee_ids": {str(k): v for k, v in ids["employee_ids"].items()}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
