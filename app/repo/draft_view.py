"""관리자 화면용 편성안 읽기 (GET /api/drafts/{id}). 쓰기는 그래프 소유 app/repo/drafts.py 가 한다 — 여기는 읽기만.

이름(employee_name)은 이 화면 응답에만 붙인다 (F-6: LLM 쪽에는 ID 만).
"""
from __future__ import annotations

import psycopg

from app.domain import Violation


def severity_of(check_code: str, detail: dict, stored: str | None = None) -> str:
    """validation_violations 에 severity 열이 없다 (db/001). 그래프가 detail["severity"] 를 넣었으면 그 값을 쓴다.

    없으면 REQUIREMENTS.md V 표에서 정한다 (가정 신규 — 「계약 변경 요청」에 올림):
    - HEADCOUNT_SHORTAGE: 지시 틀(detail.rule_key) 칸 = required, 설정값 칸 = warning (V-9, V9_ko 476 · 514)
    - OUTSIDE_AVAILABILITY 이고 detail.kind="regular": warning (V-7)
    - 그 밖: required
    """
    s = stored or detail.get("severity")  # G21: 저장된 열이 있으면 그 값 (그래프가 판정한 실제 값)
    if s in ("required", "warning"):
        return s
    if check_code == "HEADCOUNT_SHORTAGE":
        return "required" if detail.get("rule_key") else "warning"
    if check_code == "OUTSIDE_AVAILABILITY" and detail.get("kind") == "regular":
        return "warning"
    return "required"


def get_draft(conn: psycopg.Connection, store_id: int, draft_id: int) -> dict | None:
    d = conn.execute(
        """SELECT d.draft_id, d.period_id, d.status, d.parent_draft_id, d.confirmed_at, d.confirmed_by
           FROM shift_drafts d JOIN shift_periods p ON p.period_id = d.period_id
           WHERE d.draft_id = %s AND p.store_id = %s""", (draft_id, store_id)).fetchone()
    if d is None:
        return None
    assignments = conn.execute(
        """SELECT a.employee_id, e.name AS employee_name, a.work_date, a.kind, a.start_time, a.end_time,
                  a.break_start, a.break_end
           FROM shift_assignments a JOIN employees e ON e.employee_id = a.employee_id
           WHERE a.draft_id = %s ORDER BY a.work_date, a.employee_id""", (draft_id,)).fetchall()
    rows = conn.execute(
        """SELECT check_code, work_date, employee_id, detail, severity, allowed_by, allowed_at
           FROM validation_violations WHERE draft_id = %s ORDER BY violation_id""", (draft_id,)).fetchall()
    violations = []
    for r in rows:
        detail = r["detail"] or {}
        v = Violation(check_code=r["check_code"], severity=severity_of(r["check_code"], detail, r["severity"]),  # type: ignore[arg-type]
                      work_date=r["work_date"], employee_id=r["employee_id"], detail=detail)
        # key 는 app.domain.Violation.key 로만 만든다 → 그래프가 waived_keys 로 비교하는 문자열과 같다
        violations.append({"key": v.key, "check_code": v.check_code, "severity": v.severity, "waivable": v.waivable,
                           "work_date": v.work_date, "employee_id": v.employee_id, "detail": detail,
                           "allowed_by": r["allowed_by"], "allowed_at": r["allowed_at"]})
    return {**d, "assignments": assignments, "violations": violations}
