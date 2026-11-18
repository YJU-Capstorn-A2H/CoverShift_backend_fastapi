"""LLM 으로 가는 점장 문장에서 직원 실명을 ID 로 바꾼다 (메인 세션 소유).

근거: V9_ko 1148 「설계에서는 이름을 AI에 보내지 않는 것을 기본으로 한다」, F-6. 리뷰 A #5:
점장 자유문 · 재편성 지시문 · 지시 미리보기 원문이 그대로 LLM 에 실리고 있었다.
- 전체 이름, 공백을 뺀 이름, 공백으로 나눈 2글자 이상 조각(성 · 이름)을 찾는다. 긴 것부터 바꾼다.
- 조각이 두 사람 이상에게 겹치면(같은 성) 누구인지 정하지 않고 「[氏名]」로 바꾼다 (가정 G24).
- 이름이 아닌 단어가 우연히 같으면 그것도 바뀐다 — 문장이 어색해질 수는 있어도 이름이 새지는 않는 쪽을 골랐다.
"""
from __future__ import annotations

import re

AMBIGUOUS = "[氏名]"


def name_map(employees: list[tuple[int, str]]) -> dict[str, str]:
    owners: dict[str, set[int]] = {}
    for eid, name in employees:
        name = (name or "").strip()
        if not name:
            continue
        parts = {name, re.sub(r"\s+", "", name)} | {p for p in re.split(r"[\s　・、,]+", name) if len(p) >= 2}
        for p in parts:
            owners.setdefault(p, set()).add(eid)
    return {p: (f"ID{next(iter(ids))}" if len(ids) == 1 else AMBIGUOUS) for p, ids in owners.items()}


def mask_names(text: str | None, employees: list[tuple[int, str]]) -> str | None:
    if not text:
        return text
    m = name_map(employees)
    if not m:
        return text
    pat = re.compile("|".join(re.escape(k) for k in sorted(m, key=len, reverse=True)))
    return pat.sub(lambda mo: m[mo.group(0)], text)


def store_employees(conn, store_id: int) -> list[tuple[int, str]]:
    rows = conn.execute("SELECT employee_id, name FROM employees WHERE store_id = %s", (store_id,)).fetchall()
    return [(r["employee_id"], r["name"]) if isinstance(r, dict) else (r[0], r[1]) for r in rows]
