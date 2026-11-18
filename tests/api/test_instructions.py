"""점장 지시 → 틀 규칙 (M-2, T-4, G18). LLM 은 FakeComposer."""
from __future__ import annotations

import pytest

from app.domain import Employee, InstructionResult, parse_template_rule

from ._util import err


@pytest.fixture
def period(two_stores):
    api = two_stores
    api.h = api.stores["A"]["h"]
    api.e1 = api.employee(api.h, "실명A")
    api.e2 = api.employee(api.h, "실명B", False)
    api.other = api.employee(api.stores["B"]["h"], "남의점포")
    api.c.post("/api/store/time-rules", headers=api.h, json={"weekday": 1, "start_time": "08:00",
                                                             "end_time": "22:30", "min_headcount": 2})
    api.pid = api.period(api.h)
    return api


def _rules(api):
    return [
        parse_template_rule("r1", "not_available", {"staff_id": api.e1, "weekday": 7}, "A는 일요일 불가"),
        parse_template_rule("r2", "min_headcount", {"count": 3, "start": "17:00", "end": "20:00", "weekday": 5},
                            "금요일 저녁은 3명"),
        parse_template_rule("r3", "not_available", {"staff_id": api.other, "weekday": 1}, "남의 점포 직원"),
    ]


@pytest.mark.req("M-2", "T-4", "F-6")
def test_preview_calls_llm_once_with_id_only_context_and_saves_nothing(period):
    api = period
    api.composer.result = InstructionResult(rules=_rules(api), free_text=["가능하면 신입은 오전에"],
                                            rejected=[{"raw": {"template_key": "pair"}, "reason": "unknown"}])
    r = api.c.post(f"/api/shift-periods/{api.pid}/instructions/preview", headers=api.h, json={"text": "지시문"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rules"] == [
        {"rule_key": "r1", "template_key": "not_available", "params": {"staff_id": api.e1, "weekday": 7},
         "source_text": "A는 일요일 불가"},
        {"rule_key": "r2", "template_key": "min_headcount",
         "params": {"count": 3, "start": "17:00", "end": "20:00", "weekday": 5}, "source_text": "금요일 저녁은 3명"}]
    assert body["free_text"] == ["가능하면 신입은 오전에"]
    # LLM 이 남의 점포 직원 ID 를 내면 규칙에서 빼고 rejected 로 (가정 신규)
    assert [x["reason"] for x in body["rejected"]] == ["unknown", f"staff_id {api.other} 는 이 점포의 직원이 아닙니다"]
    # LLM 에 넘긴 문맥: 이 기간 · 이 점포 직원 ID 만 · 이름 없음 (F-6)
    assert len(api.composer.calls) == 1
    ctx, text = api.composer.calls[0]
    assert text == "지시문" and ctx.period.period_id == api.pid
    assert [e.employee_id for e in ctx.employees] == [api.e1, api.e2]
    assert all(type(e) is Employee for e in ctx.employees) and "실명" not in repr(ctx)
    assert len(ctx.time_rules) == 1 and ctx.availability == ()
    assert api.sql("SELECT count(*) AS n FROM manager_actions")[0]["n"] == 0  # 미리보기는 저장 안 함


@pytest.mark.req("M-2", "T-4")
def test_confirm_revalidates_and_stores_rules_confirmed(period):
    api = period
    rules = [{"rule_key": "r1", "template_key": "not_available", "params": {"staff_id": api.e1, "weekday": 7},
              "source_text": "A는 일요일 불가"},
             {"rule_key": "r2", "template_key": "max_consecutive", "params": {"shift": "late", "limit": 2},
              "source_text": "늦은 근무 2연속 금지"}]
    assert api.c.get(f"/api/shift-periods/{api.pid}/instructions", headers=api.h).json() is None
    r = api.c.post(f"/api/shift-periods/{api.pid}/instructions/confirm", headers=api.h,
                   json={"text": "원문", "rules": rules, "free_text": ["참고 문장"]})
    assert r.status_code == 201
    aid = r.json()["action_id"]
    row = api.sql("SELECT period_id, manager_id, action, instruction, rules FROM manager_actions")[0]
    assert row["action"] == "rules_confirmed" and row["manager_id"] == api.stores["A"]["manager_id"]
    assert row["instruction"] == "원문" and row["rules"]["free_text"] == ["참고 문장"]
    # 저장된 규칙은 그래프가 그대로 parse_template_rule 에 다시 넣을 수 있는 모양
    for x in row["rules"]["rules"]:
        parse_template_rule(x["rule_key"], x["template_key"], x["params"], x["source_text"])
    got = api.c.get(f"/api/shift-periods/{api.pid}/instructions", headers=api.h).json()
    assert got["action_id"] == aid and got["text"] == "원문" and [x["rule_key"] for x in got["rules"]] == ["r1", "r2"]
    # 다시 확정하면 마지막 것이 보인다
    r2 = api.c.post(f"/api/shift-periods/{api.pid}/instructions/confirm", headers=api.h,
                    json={"text": "두 번째", "rules": [], "free_text": []})
    assert api.c.get(f"/api/shift-periods/{api.pid}/instructions", headers=api.h).json()["action_id"] == r2.json()[
        "action_id"]


@pytest.mark.req("M-2", "T-4")
def test_confirm_rejects_rules_that_fail_template_check(period):
    api = period
    bad = [
        {"rule_key": "x", "template_key": "pair", "params": {}},  # 없는 틀
        {"rule_key": "x", "template_key": "not_available", "params": {"staff_id": api.e1}},  # weekday/date 둘 다 없음
        {"rule_key": "x", "template_key": "max_consecutive", "params": {"limit": 0}},  # 범위 밖
        {"rule_key": "x", "template_key": "min_headcount",
         "params": {"count": 2, "start": "09:15", "end": "10:00"}},  # 30분 단위 아님
        {"rule_key": "x", "template_key": "not_available",
         "params": {"staff_id": api.e1, "weekday": 1, "extra": 1}},  # 모르는 키
        {"rule_key": "x", "template_key": "not_available", "params": {"staff_id": api.other, "weekday": 1}},  # 남의 점포
        {"rule_key": "x", "template_key": "not_available", "params": {"staff_id": api.e1, "date": "2027-01-03"}},  # 다른 달
    ]
    for rule in bad:
        r = api.c.post(f"/api/shift-periods/{api.pid}/instructions/confirm", headers=api.h,
                       json={"text": "t", "rules": [rule], "free_text": []})
        assert err(r) == (422, "INVALID_RULE"), rule
    dup = {"rule_key": "r1", "template_key": "not_available", "params": {"staff_id": api.e1, "weekday": 7}}
    assert err(api.c.post(f"/api/shift-periods/{api.pid}/instructions/confirm", headers=api.h,
                          json={"text": "t", "rules": [dup, dup], "free_text": []})) == (422, "INVALID_RULE")
    assert api.sql("SELECT count(*) AS n FROM manager_actions")[0]["n"] == 0


@pytest.mark.req("M-2", "A-19")
def test_preview_llm_failure_is_502_with_error_shape(period):
    api = period
    api.composer.error = TimeoutError("llm timeout")
    assert err(api.c.post(f"/api/shift-periods/{api.pid}/instructions/preview", headers=api.h,
                          json={"text": "지시"})) == (502, "LLM_ERROR")
    assert err(api.c.post(f"/api/shift-periods/{api.pid}/instructions/preview", headers=api.h,
                          json={"text": ""})) == (422, "VALIDATION_ERROR")
