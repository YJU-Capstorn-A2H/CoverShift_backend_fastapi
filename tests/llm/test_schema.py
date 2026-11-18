"""출력 형식 고정 (C2) + 코드로 다시 검사: 지키는 경우 / 일부러 깨뜨리는 경우."""
from __future__ import annotations

import json
from datetime import date, time

import pytest

from app.llm.schema import (
    draft_schema,
    instruction_schema,
    parse_draft,
    parse_draft_text,
    unsupported_keywords,
)


def _row(staff_id=1, d="2026-11-02", s="08:00", e="17:00"):
    return {"staff_id": staff_id, "date": d, "start": s, "end": e}


@pytest.mark.req("M-1")
def test_valid_draft_parses_to_work_assignments(small_ctx):
    data = {"assignments": [_row(), _row(3, "2026-11-02", "17:00", "22:00")], "shortages": [], "notes": ["n1"]}
    r = parse_draft(small_ctx, data)
    assert r.ok and r.error is None
    assert [(a.employee_id, a.work_date, a.kind, a.start_time, a.end_time) for a in r.assignments] == [
        (1, date(2026, 11, 2), "work", time(8, 0), time(17, 0)),
        (3, date(2026, 11, 2), "work", time(17, 0), time(22, 0)),
    ]
    assert r.notes == ["n1"]
    assert r.usage["n_dropped"] == 0


@pytest.mark.req("M-1")
@pytest.mark.parametrize("bad,label", [
    (_row(staff_id=99), "不明なID"),
    (_row(d="2026-12-01"), "期間外の日付"),
    (_row(d="2026-11-31"), "期間外の日付"),
    (_row(s="08:15"), "30分単位の時刻でない"),
    (_row(e="8:00"), "30分単位の時刻でない"),
    (_row(s="17:00", e="17:00"), "終了が開始より後でない"),
    (_row(s="18:00", e="17:00"), "終了が開始より後でない"),
    ({"staff_id": "1", "date": "2026-11-02", "start": "08:00", "end": "17:00"}, "形が違う"),
    ({"staff_id": 1, "date": "2026-11-02"}, "形が違う"),
])
def test_invalid_rows_are_dropped_and_counted_in_notes(small_ctx, bad, label):
    r = parse_draft(small_ctx, {"assignments": [_row(), bad], "shortages": [], "notes": []})
    assert r.ok
    assert len(r.assignments) == 1  # 좋은 행만 남는다
    assert r.usage["n_dropped"] == 1
    assert any("1行除いた" in n and label in n for n in r.notes), r.notes


@pytest.mark.req("V-8")
def test_duplicate_rows_are_not_dropped_so_validate_can_catch_double_booking(small_ctx):
    """같은 사람 같은 날 2행은 형식 검사에서 지우지 않는다 — 지우면 DOUBLE_BOOKING 이 가려진다."""
    r = parse_draft(small_ctx, {"assignments": [_row(), _row(s="09:00", e="18:00")], "shortages": [], "notes": []})
    assert r.ok and len(r.assignments) == 2 and r.usage["n_dropped"] == 0


@pytest.mark.req("F-4")
def test_zero_valid_rows_is_empty(small_ctx):
    assert parse_draft(small_ctx, {"assignments": [], "shortages": [], "notes": []}).error == "empty"
    r = parse_draft(small_ctx, {"assignments": [_row(staff_id=99)], "shortages": [], "notes": []})
    assert not r.ok and r.error == "empty" and r.assignments == []
    assert any("不明なID 1行" in n for n in r.notes)


@pytest.mark.req("F-4")
@pytest.mark.parametrize("text", ["", "not json", "[1,2]", '{"shortages": []}', '{"assignments": {}}'])
def test_unreadable_output_is_parse_error(small_ctx, text):
    r = parse_draft_text(small_ctx, text)
    assert not r.ok and r.error == "parse_error"


@pytest.mark.req("F-14")
def test_shortages_and_notes_are_passed_through_in_screen_shape(small_ctx):
    data = {"assignments": [_row()], "notes": ["希望休を1日通せなかった"],
            "shortages": [{"date": "2026-11-20", "start": "08:00", "end": "09:00", "need": 3, "have": 2, "reason": "人が足りない"},
                          {"date": "2026-11-21"}]}  # 모양이 틀린 참고 행은 조용히 빠진다 (검사 대상이 아님)
    r = parse_draft(small_ctx, data)
    assert r.shortages == [{"work_date": "2026-11-20", "start_time": "08:00", "end_time": "09:00",
                            "need": 3, "have": 2, "reason": "人が足りない"}]
    assert r.notes == ["希望休を1日通せなかった"]
    assert r.usage["n_dropped"] == 0  # shortages 가 틀려도 assignments 버린 수에는 안 들어간다


@pytest.mark.req("M-1")
def test_draft_schema_uses_only_supported_keywords_and_enums(azuki_ctx):
    s = draft_schema(azuki_ctx)
    assert unsupported_keywords(s) == []
    item = s["properties"]["assignments"]["items"]["properties"]
    assert item["staff_id"]["enum"] == sorted(e.employee_id for e in azuki_ctx.employees)
    assert item["date"]["enum"][0] == "2026-11-01" and item["date"]["enum"][-1] == "2026-11-30"
    assert item["start"]["enum"][0] == "08:00" and item["end"]["enum"][-1] == "22:30"
    assert all(t[-2:] in ("00", "30") for t in item["start"]["enum"])
    json.dumps(s)  # JSON 으로 보낼 수 있는 값만


@pytest.mark.req("M-2")
def test_instruction_schema_uses_only_supported_keywords():
    assert unsupported_keywords(instruction_schema()) == []


@pytest.mark.req("M-1")
def test_schema_checker_catches_unsupported_keyword():
    """깨뜨리는 쪽: 검사 함수가 실제로 minimum · additionalProperties 누락을 잡는지."""
    bad = {"type": "object", "additionalProperties": False,
           "properties": {"n": {"type": "integer", "minimum": 0}, "o": {"type": "object", "properties": {}}},
           "required": ["n", "o"]}
    found = unsupported_keywords(bad)
    assert any(f.endswith("minimum") for f in found)
    assert any("without additionalProperties" in f for f in found)
