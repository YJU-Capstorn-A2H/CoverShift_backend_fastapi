# app/graph_azuki_convert.py -- アズキのグラフ(app/graph/)と、승혁 の runner・API・画面の間で、「値の形」を変える関数だけを集めたファイル
#   ・DB にも LangGraph にも触らない、ふつうの関数だけ(だから、すぐ試験できる)
#   ・アズキのグラフの形:  勤務 {"staff_id", "date", "start", "end"} / 止まる所 approve・contact / 再開 {"decision", ...}
#   ・승혁 の形:           勤務 {"employee_id", "work_date", "kind", "start_time", "end_time"} / 止まる所 {"type": "review"|"contact", ...}
#   ・使うのは app/graph_azuki_bridge.py(橋)。app/graph/ の中身は、変えない。
from __future__ import annotations

from typing import Any


def _hm(v: Any) -> str | None:
    """'09:00' / '09:00:00' / time → '09:00'。無ければ None。"""
    if v is None:
        return None
    return str(v)[:5]


# ---------------------------------------------------------------- 勤務(1行)
def rows_to_seung(rows: list[dict]) -> list[dict]:
    """アズキの勤務 → 승혁 の勤務(DBに入れられる形)。"""
    return [{"employee_id": int(r["staff_id"]), "work_date": str(r["date"]), "kind": "work",
             "start_time": _hm(r["start"]), "end_time": _hm(r["end"])} for r in rows]


def rows_to_azuki(rows: list[dict]) -> list[dict]:
    """승혁 の勤務 → アズキの勤務。アズキのグラフは「出勤」だけを持つので、kind が work でない行(有休など)は捨てる。"""
    out = []
    for r in rows:
        if r.get("kind", "work") != "work":
            continue
        out.append({"staff_id": int(r["employee_id"]), "date": str(r["work_date"]),
                    "start": _hm(r.get("start_time")), "end": _hm(r.get("end_time"))})
    return out


# ---------------------------------------------------------------- 止まる所 approve → ReviewWait
def _result_for_screen(result: str | None, draft: list) -> str:
    """アズキの result(valid / retry / no_staff / limit_reached)→ 승혁 の result(valid / retry / limit_reached / draft_empty)。"""
    if not draft:
        return "draft_empty"
    return "valid" if result == "valid" else "limit_reached"


def review_wait(value: dict, *, values: dict, draft_id: int | None, violations: list[dict],
                blocked: list[dict], limit: int) -> dict:
    """アズキの approve の止まる値 → 승혁 の ReviewWait(API_CONTRACT)。
    violations / blocked は、呼ぶ側が 승혁 の検査(app.validate)で作った、コードつきの違反。アズキの problems(文章)は、notes に回す。"""
    left = value.get("redraft_left", limit)
    notes = list(value.get("blocked_reasons") or [])
    return {
        "type": "review",
        "draft_id": draft_id,
        "result": _result_for_screen(values.get("result"), value.get("draft_store_shift") or []),
        "violations": violations,
        "blocked": blocked,
        "shortages": [],
        "notes": notes,
        "free_text_guidelines": [],
        "auto_retry_count": values.get("return_count", 0),
        "manager_request_count": limit - left,
        "manager_request_limit": limit,
    }


# ---------------------------------------------------------------- 再開(승혁 の答え → アズキの答え)
_CONTACT_ANSWER = {"yes": "可能", "no": "断り", "expired": "不確か"}


def resume_to_azuki(payload: dict) -> Any:
    """승혁 の resume payload → アズキの interrupt が受け取る値。
    review: {"decision", "manager_id", "draft_id", "waived_keys"?, "instruction"?, "assignments"?}  → {"decision", "instruction"?, "assignments"?}
    contact: {"contact_id", "answer": yes|no|expired}  → "可能" | "断り" | "不確か" """
    if "contact_id" in payload:
        answer = payload.get("answer")
        if answer not in _CONTACT_ANSWER:
            raise ValueError(f"不正な連絡の返事: {answer!r}")
        return _CONTACT_ANSWER[answer]
    decision = payload.get("decision")
    out: dict = {"decision": decision}
    if decision == "redraft_requested":
        out["instruction"] = (payload.get("instruction") or "").strip()
    elif decision == "edit_saved":
        out["assignments"] = rows_to_azuki(payload.get("assignments") or [])
    return out
