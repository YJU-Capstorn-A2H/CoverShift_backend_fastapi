"""manager_actions 중 틀 규칙 확정(rules_confirmed) 만 (M-2, G2, G18).

승인 · 재편성 요청 · 편집 저장 행은 그래프(app/graph · app/repo/drafts.py)가 resume 받은 뒤 쓴다 — API 는 쓰지 않는다
(같은 조작이 두 곳에서 기록되면 one_approval_per_draft 가 두 번째를 거부한다).
"""
from __future__ import annotations

import psycopg
from psycopg.types.json import Jsonb


def insert_rules_confirmed(conn: psycopg.Connection, *, period_id: int, manager_id: int, text: str,
                           rules: list[dict], free_text: list[str]) -> int:
    """rules 열 모양은 db/002 주석 그대로: {"rules":[{rule_key,template_key,params,source_text}], "free_text":[...]}."""
    return conn.execute(
        """INSERT INTO manager_actions (period_id, manager_id, action, instruction, rules)
           VALUES (%s, %s, 'rules_confirmed', %s, %s) RETURNING action_id""",
        (period_id, manager_id, text, Jsonb({"rules": rules, "free_text": free_text}))).fetchone()["action_id"]


def latest_rules_confirmed(conn: psycopg.Connection, period_id: int) -> dict | None:
    """마지막 확인본 (G18 「편성 시작 때 마지막 확인본을 State 에 고정」 — 고정은 그래프가 한다)."""
    return conn.execute(
        """SELECT action_id, instruction, rules, created_at FROM manager_actions
           WHERE period_id = %s AND action = 'rules_confirmed'
           ORDER BY created_at DESC, action_id DESC LIMIT 1""", (period_id,)).fetchone()
