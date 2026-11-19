"""MCP 읽기 도구의 본체 (M-7, A-18). 메인 세션 소유. MCP 서버(app/mcp_server.py)는 이 함수를 감싸기만 한다.

- 이름 열을 읽지 못하는 DB 롤(covershift_llm_read, db/001)로 SET ROLE 한 뒤 읽는다 → 실수로 name 을 SELECT 하면 DB 가 거부.
- 돌려주는 값은 JSON 으로 바꿀 수 있는 dict 만. staff_info_to_domain() 으로 domain 형으로 바꾼다.
"""
from __future__ import annotations

from datetime import date, time

from app.db import connect
from app.domain import Availability, Employee


def get_staff_info(period_id: int, url: str | None = None) -> dict:
    with connect(url) as c:
        c.execute("SET ROLE covershift_llm_read")
        p = c.execute("SELECT period_id, store_id, target_month FROM shift_periods WHERE period_id = %s",
                      (period_id,)).fetchone()
        if p is None:
            raise LookupError(f"period {period_id} not found")
        emps = c.execute(
            """SELECT employee_id, is_regular, has_qualification, closing_ok, weekly_days, fixed_weekdays
               FROM employees WHERE store_id = %s ORDER BY employee_id""", (p["store_id"],)).fetchall()
        start = p["target_month"]
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        av = c.execute(
            """SELECT a.employee_id, a.work_date, a.kind, a.start_time, a.end_time
               FROM availability_submissions a JOIN employees e USING (employee_id)
               WHERE e.store_id = %s AND a.work_date >= %s AND a.work_date < %s
               ORDER BY a.employee_id, a.work_date""", (p["store_id"], start, end)).fetchall()
        c.execute("RESET ROLE")

    def t(v: time | None) -> str | None:
        return v.strftime("%H:%M") if v else None

    return {
        "period_id": period_id,
        "employees": [{**e, "fixed_weekdays": list(e["fixed_weekdays"]) if e["fixed_weekdays"] else None} for e in emps],
        "availability": [{**a, "work_date": a["work_date"].isoformat(), "start_time": t(a["start_time"]),
                          "end_time": t(a["end_time"])} for a in av],
    }


def staff_info_to_domain(d: dict) -> tuple[tuple[Employee, ...], tuple[Availability, ...]]:
    emps = tuple(Employee(e["employee_id"], e["is_regular"], e["has_qualification"], e["closing_ok"], e["weekly_days"],
                          tuple(e["fixed_weekdays"]) if e["fixed_weekdays"] else None) for e in d["employees"])
    av = tuple(Availability(a["employee_id"], date.fromisoformat(a["work_date"]), a["kind"],
                            time.fromisoformat(a["start_time"]) if a["start_time"] else None,
                            time.fromisoformat(a["end_time"]) if a["end_time"] else None) for a in d["availability"])
    return emps, av
