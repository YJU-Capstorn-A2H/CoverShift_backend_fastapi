"""편성 그래프가 쓰는 DB 읽기・쓰기 (담당: graph 에이전트).

- 모든 함수는 호출자가 넘긴 연결(conn)에서 돈다. 커밋은 호출자가 한다 (app.db.connect() 의 with 블록이 끝날 때).
  → 한 노드 = 한 트랜잭션. commit_notify 는 확정・허용・승인 기록・통지를 한 트랜잭션으로 묶는다 (F-9).
- 값은 State 와 같은 JSON 모양(dict, 날짜 'YYYY-MM-DD', 시각 'HH:MM')으로 받고 돌려준다.
- 이름(employees.name)은 읽지 않는다 (F-6, V9_ko 483).
"""
from __future__ import annotations

import json
from datetime import date, time

from app.domain import Violation

# ---------------------------------------------------------------- 형 변환 (JSON 문자열 ↔ DB 값)


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _t(v: str | None) -> time | None:
    return time.fromisoformat(v) if v else None


def _hm(v: time | None) -> str | None:
    return v.strftime("%H:%M") if v is not None else None


# ---------------------------------------------------------------- collect (F-17: 시작 시점에 고정)


def load_period(conn, period_id: int) -> dict:
    """shift_periods 1행 (V9_ko 267-277). 없으면 LookupError."""
    r = conn.execute(
        """SELECT period_id, store_id, target_month, designated_off_days, off_request_limit, work_request_limit
           FROM shift_periods WHERE period_id = %s""", (period_id,)).fetchone()
    if r is None:
        raise LookupError(f"period {period_id} not found")
    return {**r, "target_month": r["target_month"].isoformat()}


def load_time_rules(conn, store_id: int) -> list[dict]:
    """store_time_rules 전부 (V9_ko 234-245). 요일・시작 순."""
    rows = conn.execute(
        """SELECT rule_id, weekday, start_time, end_time, min_headcount, min_qualified
           FROM store_time_rules WHERE store_id = %s ORDER BY weekday, start_time""", (store_id,)).fetchall()
    return [{**r, "start_time": _hm(r["start_time"]), "end_time": _hm(r["end_time"])} for r in rows]


def latest_rules(conn, period_id: int) -> dict | None:
    """그 기간의 마지막 rules_confirmed (G18). 없으면 None.

    돌려주는 값: {"action_id", "rules": [{rule_key, template_key, params, source_text}], "free_text": [str]}
    """
    r = conn.execute(
        """SELECT action_id, rules FROM manager_actions
           WHERE period_id = %s AND action = 'rules_confirmed'
           ORDER BY created_at DESC, action_id DESC LIMIT 1""", (period_id,)).fetchone()
    if r is None:
        return None
    body = r["rules"] or {}
    return {"action_id": r["action_id"], "rules": list(body.get("rules") or []),
            "free_text": [str(x) for x in body.get("free_text") or []]}


# ---------------------------------------------------------------- 편성안 저장 (F-1, G4)


def save_draft(conn, *, period_id: int, assignments: list[dict], violations: list[dict],
               parent_draft_id: int | None) -> int:
    """편성안 1개 + 배정 + 그 안의 위반을 저장하고 draft_id 를 돌려준다.

    - 검사가 끝난 안만 이 함수로 온다 (F-1, V9_ko 93). 위반도 같이 쓴다 (G4: 검사 통과・상한 도달・편집 저장 때).
    - (가정 신규) shift_assignments 의 PK 가 (draft_id, employee_id, work_date) 라 같은 사람・같은 날 두 행은 저장할 수 없다
      (V9_ko 475 「v14는 PK로 1인 1일 1행이라 이미 막힘」). 그런 안은 DOUBLE_BOOKING(예외 불가) 위반을 안고 있으므로
      승인은 어차피 막힌다. 저장할 때는 (직원, 날짜)별 첫 행만 남기고 나머지는 버린다 — 위반 기록은 그대로 남는다.
    """
    d = conn.execute(
        "INSERT INTO shift_drafts (period_id, parent_draft_id) VALUES (%s, %s) RETURNING draft_id",
        (period_id, parent_draft_id)).fetchone()["draft_id"]
    seen: set[tuple[int, str]] = set()
    rows = []
    for a in assignments:
        k = (a["employee_id"], a["work_date"])
        if k in seen:
            continue
        seen.add(k)
        rows.append((d, a["employee_id"], _d(a["work_date"]), a.get("kind", "work"), _t(a.get("start_time")),
                     _t(a.get("end_time")), _t(a.get("break_start")), _t(a.get("break_end"))))
    if rows:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO shift_assignments
                   (draft_id, employee_id, work_date, kind, start_time, end_time, break_start, break_end)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", rows)
    if violations:
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO validation_violations (draft_id, check_code, work_date, employee_id, detail, severity)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s)""",
                [(d, v["check_code"], _d(v.get("work_date")), v.get("employee_id"),
                  json.dumps(v.get("detail") or {}, ensure_ascii=False), v.get("severity")) for v in violations])
    return d


def load_assignments(conn, draft_id: int) -> list[dict]:
    """저장된 안의 배정 (State 와 같은 JSON 모양). 확정 통지는 이것을 쓴다 — DB 에 확정된 그대로 보낸다."""
    rows = conn.execute(
        """SELECT employee_id, work_date, kind, start_time, end_time, break_start, break_end
           FROM shift_assignments WHERE draft_id = %s ORDER BY employee_id, work_date""", (draft_id,)).fetchall()
    return [{**r, "work_date": r["work_date"].isoformat(), "start_time": _hm(r["start_time"]),
             "end_time": _hm(r["end_time"]), "break_start": _hm(r["break_start"]), "break_end": _hm(r["break_end"])}
            for r in rows]


# ---------------------------------------------------------------- 점장 조작 기록 (G2)


def record_action_once(conn, *, period_id: int, draft_id: int, manager_id: int, action: str,
                       instruction: str | None = None) -> int:
    """redraft_requested / edit_saved 를 manager_actions 에 1행 남긴다. 같은 (draft_id, action) 이 이미 있으면 그 행을 쓴다.

    한 편성안에 재편성 요청・편집 저장은 한 번씩만 일어난다 (그 뒤에는 새 안이 생긴다) → 노드가 다시 돌아도 행이 늘지 않는다.
    """
    if action not in ("redraft_requested", "edit_saved"):
        raise ValueError(action)
    r = conn.execute("SELECT action_id FROM manager_actions WHERE draft_id = %s AND action = %s",
                     (draft_id, action)).fetchone()
    if r is not None:
        return r["action_id"]
    return conn.execute(
        """INSERT INTO manager_actions (period_id, draft_id, manager_id, action, instruction)
           VALUES (%s, %s, %s, %s, %s) RETURNING action_id""",
        (period_id, draft_id, manager_id, action, instruction)).fetchone()["action_id"]


# ---------------------------------------------------------------- 확정 (F-7, F-9, A-16)


def _row_key(r: dict) -> tuple[str, bool]:
    v = Violation(r["check_code"], "required", r["work_date"], r["employee_id"], r["detail"] or {})
    return v.key, v.waivable


def confirm_draft(conn, *, period_id: int, draft_id: int, manager_id: int, waived_keys: list[str]) -> None:
    """확정 저장 (V9_ko 88 commit_notify 앞부분). 다시 불러도 결과가 같다 (중단 뒤 노드 재실행 대비).

    1) shift_drafts: status='confirmed', confirmed_at/by (V9_ko 54 승인자 ID). 기간당 확정 1개는 DB 부분 유일 인덱스가 막는다.
    2) validation_violations: 점장이 허용한 위반에 allowed_by/at (G4). 예외 불가 코드는 허용으로 쓰지 않는다 (V9_ko 479).
    3) manager_actions 'approved' — 같은 안에 두 번 들어가지 않는다 (one_approval_per_draft, V9_ko 505).
    """
    cur = conn.execute(
        """UPDATE shift_drafts SET status = 'confirmed', confirmed_at = now(), confirmed_by = %s
           WHERE draft_id = %s AND period_id = %s AND status = 'draft'""", (manager_id, draft_id, period_id))
    if cur.rowcount == 0:
        r = conn.execute("SELECT status FROM shift_drafts WHERE draft_id = %s AND period_id = %s",
                         (draft_id, period_id)).fetchone()
        if r is None:
            raise LookupError(f"draft {draft_id} (period {period_id}) not found")
        # status='confirmed' → 이미 확정됨(재실행). 그대로 아래로 — 허용・승인 기록도 멱등이다.
    waived = set(waived_keys)
    rows = conn.execute(
        """SELECT violation_id, check_code, work_date, employee_id, detail FROM validation_violations
           WHERE draft_id = %s AND allowed_by IS NULL""", (draft_id,)).fetchall()
    ids = [r["violation_id"] for r in rows if (kw := _row_key(r))[1] and kw[0] in waived]
    if ids:
        conn.execute("UPDATE validation_violations SET allowed_by = %s, allowed_at = now() WHERE violation_id = ANY(%s)",
                     (manager_id, ids))
    conn.execute(
        """INSERT INTO manager_actions (period_id, draft_id, manager_id, action) VALUES (%s, %s, %s, 'approved')
           ON CONFLICT (draft_id) WHERE action = 'approved' DO NOTHING""", (period_id, draft_id, manager_id))


# ---------------------------------------------------------------- 직원 연락 (M-6, G8 — 기본 꺼 둠)


def ask_contact(conn, *, period_id: int, employee_id: int, work_date: str, start_time: str, end_time: str) -> int:
    """contact_log 에 질문 1건. 같은 (기간, 직원, 날짜, 시작) 이 이미 있으면 그 행의 contact_id (UNIQUE, db/002)."""
    r = conn.execute(
        """INSERT INTO contact_log (period_id, employee_id, work_date, start_time, end_time)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (period_id, employee_id, work_date, start_time) DO NOTHING RETURNING contact_id""",
        (period_id, employee_id, _d(work_date), _t(start_time), _t(end_time))).fetchone()
    if r is not None:
        return r["contact_id"]
    return conn.execute(
        """SELECT contact_id FROM contact_log
           WHERE period_id = %s AND employee_id = %s AND work_date = %s AND start_time = %s""",
        (period_id, employee_id, _d(work_date), _t(start_time))).fetchone()["contact_id"]


def answer_contact(conn, *, contact_id: int, status: str) -> None:
    """답을 기록한다. 이미 답이 있으면 덮어쓰지 않는다 (먼저 온 답이 이긴다)."""
    if status not in ("accepted", "declined", "expired"):
        raise ValueError(status)
    conn.execute("""UPDATE contact_log SET status = %s, answered_at = now()
                    WHERE contact_id = %s AND status = 'asked'""", (status, contact_id))
