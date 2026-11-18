"""프롬프트 문장 (순수 함수). 지키는 경우와 일부러 깨뜨리는 경우를 같이 본다."""
from __future__ import annotations

import dataclasses
import re
from datetime import date, time

import pytest

from app.domain import Assignment, Violation, ViolationGroup, parse_template_rule
from app.llm.prompt import (
    GIVE_UP_ORDER,
    KEEP_OTHER_CELLS,
    build_compose_request,
    build_instruction_request,
    build_system_text,
    make_retry_text,
    request_text,
    staff_payload,
    violation_text,
)


def _all_text(ctx, previous=None, history=(), instruction=None) -> str:
    return request_text(*build_compose_request(ctx, previous, list(history), instruction))


def _names_in(text: str, names: list[str]) -> list[str]:
    return [n for n in names if n in text]


def _group(v: Violation, rounds) -> ViolationGroup:
    return ViolationGroup(key=v.key, check_code=v.check_code, severity=v.severity, rounds=tuple(rounds), example=v)


V_QUAL = Violation("QUAL_MISSING", "required", date(2026, 11, 3), None, {"time": "13:00", "need": 1, "have": 0})
V_DAYS = Violation("REGULAR_DAYS_MISMATCH", "required", None, 2, {"expected": 21, "actual": 20})
V_HEAD = Violation("HEADCOUNT_SHORTAGE", "warning", date(2026, 11, 4), None, {"start": "09:00", "end": "13:00", "need": 4, "have": 3})


# ---------------------------------------------------------------- F-6 실명 없음

@pytest.mark.req("F-6")
def test_prompt_has_no_employee_names(azuki_ctx, azuki_names):
    prev = [Assignment(1, date(2026, 11, 2), "work", time(8), time(17))]
    hist = [_group(V_QUAL, [1, 2]), _group(V_DAYS, [1])]
    for text in (_all_text(azuki_ctx), _all_text(azuki_ctx, prev, hist, "もう少し均等に"),
                 request_text(*build_instruction_request(azuki_ctx, "3番は日曜不可"))):
        assert _names_in(text, azuki_names) == []
        assert "staff_name" not in text and '"name"' not in text
    keys = {k for item in staff_payload(azuki_ctx) for k in item}
    assert keys <= {"staff_id", "employment", "qualified", "closing_ok", "weekly_days", "fixed_weekdays", "requests"}


@pytest.mark.req("F-6")
def test_name_detector_catches_a_name_when_one_leaks(azuki_ctx, azuki_names):
    """깨뜨리는 쪽: 점장 자유문에 실명이 있으면 그대로 실린다 (V9_ko 478 「문장 그대로」). 검출기가 그것을 잡는지 본다.
    = 알려진 경로: free_text · 재편성 지시문에 쓴 실명은 막지 않는다 (보고서 「한계」)."""
    leaked = dataclasses.replace(azuki_ctx, free_text_guidelines=(f"{azuki_names[0]}は午前だけ",))
    assert _names_in(_all_text(leaked), azuki_names) == [azuki_names[0]]


# ---------------------------------------------------------------- 설정에서 만드는 문 · F-12

@pytest.mark.req("F-12")
def test_give_up_order_is_in_system_prompt_in_order(azuki_ctx):
    text = build_system_text(azuki_ctx)
    pos = [text.index(f"{i}. {s}") for i, s in enumerate(GIVE_UP_ORDER, 1)]
    assert pos == sorted(pos)
    assert "下のほうから諦めること" in text
    assert [s.split("(")[0] for s in GIVE_UP_ORDER] == [
        "資格者の配置", "社員の出勤日数を守る", "社員の希望休と有給・半休", "時間帯ごとの最低人数",
        "パート・バイトの申請を、できるだけ多く入れる", "パート・バイトの出勤回数の差を小さくする"]


@pytest.mark.req("V-5", "V-2", "V-9")
def test_settings_derived_text(azuki_ctx):
    text = build_system_text(azuki_ctx)
    assert "出勤は、ちょうど21日にすること" in text  # 30 − 9 (V-5)
    assert "毎日 08:00〜22:30は、qualified が true の人が、どの30分の区切りにも1人以上いること" in text  # 이어진 3칸을 합침 (V-2)
    assert "【時間帯ごとの最低人数(できるだけ守る目標。検査では警告だけ)】" in text  # 설정값 = 경고 (V-9)
    assert "毎日 09:00〜13:00は、同時にいる人数を4人以上にすること" in text
    assert "出力する前に" not in text  # mix1: 자기 검산 지시 삭제
    assert "まず案を出すこと" in text  # mix1: 길 주기


@pytest.mark.req("V-2")
def test_zero_min_qualified_gives_no_qualification_sentence(azuki_ctx):
    """깨뜨리는 쪽: min_qualified=0 이면 자격 문장이 사라진다 (G10)."""
    ctx = dataclasses.replace(azuki_ctx, time_rules=tuple(dataclasses.replace(r, min_qualified=0) for r in azuki_ctx.time_rules))
    assert "【資格のルール" not in build_system_text(ctx)


@pytest.mark.req("V-5")
def test_work_days_follow_designated_off_days(azuki_ctx):
    ctx = dataclasses.replace(azuki_ctx, period=dataclasses.replace(azuki_ctx.period, designated_off_days=10))
    text = build_system_text(ctx)
    assert "ちょうど20日" in text and "ちょうど21日" not in text


# ---------------------------------------------------------------- 틀 규칙 vs 자유문 (T-4, M-2)

def _rules():
    return (
        parse_template_rule("r1", "not_available", {"staff_id": 3, "weekday": 7}, "3番は日曜不可"),
        parse_template_rule("r2", "min_headcount", {"count": 3, "start": "17:00", "end": "22:30", "weekday": 5}, "金曜夜は3人"),
        parse_template_rule("r3", "max_consecutive", {"limit": 4}, "連勤は4日まで"),
        parse_template_rule("r4", "max_consecutive", {"limit": 1, "shift": "late", "staff_id": 2}, "2番の遅番は連続なし"),
    )


@pytest.mark.req("T-4", "M-2")
def test_template_rules_are_guaranteed_and_free_text_is_reference(azuki_ctx):
    ctx = dataclasses.replace(azuki_ctx, template_rules=_rules(), free_text_guidelines=("ベテランと新人を組ませて",))
    text = build_system_text(ctx)
    assert "【店長の指示(必ず守る。コードが検査する)】" in text
    assert "- [r1] ID3は日曜曜日に入れない。" not in text  # 요일 이름이 두 번 붙지 않는다
    assert "- [r1] ID3は日曜日に入れない。" in text
    assert "- [r2] 金曜日 17:00〜22:30は、同時にいる人数を3人以上にする" in text
    assert "- [r3] 全員は、連続出勤を4日までにする。" in text
    assert "- [r4] ID2は、遅番(その日の閉店時刻に終わる勤務)の連続を1回までにする。" in text
    assert "ただし【店長の指示】に人数がある時間帯は、指示の人数が優先で、必ず守る" in text  # T-3 이 설정값을 덮어씀
    free = text.index("【店長のメモ(参考・保証なし。コードは検査しない。")
    assert text.index("- ベテランと新人を組ませて", free) > free
    assert "3番は日曜不可" not in text  # 틀 규칙의 원문(source_text)은 싣지 않는다 (실명 경로를 줄임)


@pytest.mark.req("T-4")
def test_no_rules_no_sections(azuki_ctx):
    """깨뜨리는 쪽: 틀 규칙 · 자유문이 없으면 두 칸이 모두 없다 (빈 칸으로 「보장」을 보이지 않는다)."""
    text = build_system_text(azuki_ctx)
    assert "【店長の指示" not in text and "参考・保証なし" not in text and "指示の人数が優先" not in text


# ---------------------------------------------------------------- 재시도 (F-5)

@pytest.mark.req("F-5")
def test_retry_prompt_has_previous_grouped_history_rounds_and_keep_sentence(azuki_ctx):
    prev = [Assignment(1, date(2026, 11, 2), "work", time(8), time(17)),
            Assignment(2, date(2026, 11, 19), "paid_leave")]
    hist = [_group(V_QUAL, [1, 2, 3]), _group(V_DAYS, [1]), _group(V_HEAD, [3])]
    text = make_retry_text(azuki_ctx, prev, hist, None)
    assert '【前回の案(assignments)】\n[{"staff_id":1,"date":"2026-11-02","start":"08:00","end":"17:00"}]' in text  # 유급 행은 빠짐
    assert "【3回目の検査で出た問題(全1件。今回は必ず直すこと。" in text
    assert "- 2026-11-03 13:00 資格者(qualified)が足りない(必要1・実際0)【1・2・3回目】" in text
    assert "【それより前の回だけで出た問題(全1件。今は直っている。元に戻さないこと)】" in text
    assert "- ID2 社員の出勤日数が決まりの日数と違う(actual=20, expected=21)【1回目】" in text
    assert "HEADCOUNT" not in text and "人数が足りない" not in text  # 경고는 넘기지 않는다 (mix1, 가정 신규)
    assert KEEP_OTHER_CELLS == "違反のセル以外は変えないこと。"
    assert KEEP_OTHER_CELLS in text and "案全体(assignments をすべて)をもう一度出すこと" in text


@pytest.mark.req("F-5")
def test_retry_prompt_with_manager_instruction(azuki_ctx):
    prev = [Assignment(1, date(2026, 11, 2), "work", time(8), time(17))]
    text = make_retry_text(azuki_ctx, prev, [_group(V_QUAL, [1])], "土曜の午前を厚くして")
    assert "【店長からの作り直しの指示(できるだけ反映する。コードは検査しない)】\n土曜の午前を厚くして" in text
    assert KEEP_OTHER_CELLS + "ただし、店長の指示に必要な変更はしてよい。" in text


@pytest.mark.req("F-5")
def test_first_prompt_has_no_retry_parts(azuki_ctx):
    """깨뜨리는 쪽: 첫 호출에는 직전 안 · 이력 · 「바꾸지 마라」가 없다 (있으면 재시도 문장이 새는 것)."""
    system, messages = build_compose_request(azuki_ctx, None, [], None)
    assert len(messages[0]["content"]) == 1
    text = request_text(system, messages)
    assert "前回の案" not in text and "回目の検査" not in text and KEEP_OTHER_CELLS not in text


@pytest.mark.req("F-5")
def test_system_and_staff_blocks_are_stable_between_first_and_retry(azuki_ctx):
    """캐시: system 과 직원 블록은 재시도에서도 글자 하나 바뀌지 않아야 한다 (바뀌면 캐시가 깨진다)."""
    s1, m1 = build_compose_request(azuki_ctx, None, [], None)
    s2, m2 = build_compose_request(azuki_ctx, [Assignment(1, date(2026, 11, 2), "work", time(8), time(17))],
                                   [_group(V_QUAL, [1])], "指示")
    assert s1 == s2 and s1[0]["cache_control"] == {"type": "ephemeral"}
    assert m1[0]["content"][0] == m2[0]["content"][0] and m2[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in m2[0]["content"][1]
    assert "前回の案" not in s2[0]["text"]


# ---------------------------------------------------------------- 같은 위반 → 같은 문장 (V9_ko 412-415)

@pytest.mark.req("F-5")
def test_same_violation_gives_same_sentence_regardless_of_detail_order():
    a = Violation("HEADCOUNT_SHORTAGE", "required", date(2026, 11, 6), None,
                  {"start": "17:00", "end": "22:30", "need": 3, "have": 2, "rule_key": "r2"})
    b = Violation("HEADCOUNT_SHORTAGE", "required", date(2026, 11, 6), None,
                  {"rule_key": "r2", "have": 2, "need": 3, "end": "22:30", "start": "17:00"})
    assert violation_text(a) == violation_text(b) == "2026-11-06 17:00〜22:30 同時にいる人数が足りない(店長の指示 r2)(必要3・実際2)"
    c = dataclasses.replace(a, detail={**a.detail, "have": 1})
    assert violation_text(c) != violation_text(a)  # 깨뜨리는 쪽: 값이 다르면 다른 문장


@pytest.mark.req("F-5")
@pytest.mark.parametrize("code", ["REGULAR_ABSENT", "QUAL_MISSING", "OUTSIDE_AVAILABILITY", "CLOSING_NOT_ALLOWED",
                                  "REGULAR_DAYS_MISMATCH", "HALF_LEAVE_MISMATCH", "DOUBLE_BOOKING", "HEADCOUNT_SHORTAGE",
                                  "HEADCOUNT_EXCESS", "RULE_VIOLATION"])
def test_every_known_code_has_its_own_sentence(code):
    t = violation_text(Violation(code, "required", date(2026, 11, 1), 5, {}))
    assert code not in t and t.startswith("2026-11-01 ID5 ")


@pytest.mark.req("F-5")
@pytest.mark.parametrize("v,expected", [
    (Violation("REGULAR_DAYS_MISMATCH", "required", None, 2, {"need": 21, "have": 20, "work_days": 19, "paid_leave_days": 1}),
     "ID2 社員の出勤日数が決まりの日数と違う(必要21・実際20, 出勤19日・有給1日)"),
    (Violation("OUTSIDE_AVAILABILITY", "required", date(2026, 11, 2), 11,
               {"kind": "part", "reason": "outside_time", "assigned_start": "16:00", "assigned_end": "22:00",
                "avail_kind": "available", "avail_start": "17:00", "avail_end": "22:00"}),
     "2026-11-02 ID11 申請の日・申請の時間の外に入っている(入れた時間 16:00〜22:00, 申請 出勤希望 17:00〜22:00, 申請の時間の外)"),
    (Violation("OUTSIDE_AVAILABILITY", "required", date(2026, 11, 3), 12,
               {"kind": "part", "reason": "no_submission", "assigned_start": "17:00", "assigned_end": "22:00",
                "avail_kind": None, "avail_start": None, "avail_end": None}),
     "2026-11-03 ID12 申請の日・申請の時間の外に入っている(入れた時間 17:00〜22:00, 申請 申請なし, 申請なし)"),
    (Violation("CLOSING_NOT_ALLOWED", "required", date(2026, 11, 4), 6,
               {"closing": "22:30", "assigned_start": "17:00", "assigned_end": "22:30"}),
     "2026-11-04 ID6 closing_ok が false なのに閉店時刻まで入っている(入れた時間 17:00〜22:30, 閉店 22:30)"),
    (Violation("DOUBLE_BOOKING", "required", date(2026, 11, 5), 1, {"count": 2, "overlap": True}),
     "2026-11-05 ID1 同じ人が同じ日に2つ以上入っている(時間の重なり)(2件, 時間が重なる)"),
    (Violation("RULE_VIOLATION", "required", date(2026, 11, 9), 1,
               {"rule_key": "r3", "template_key": "max_consecutive", "shift": "any", "limit": 4,
                "run_start": "2026-11-05", "run_end": "2026-11-09", "run_length": 5}),
     "2026-11-09 ID1 店長の指示に反している(店長の指示 r3)(2026-11-05〜2026-11-09 出勤5日連続・上限4)"),
    (Violation("HEADCOUNT_SHORTAGE", "warning", date(2026, 11, 6), None, {"time": "09:00", "source": "setting", "need": 4, "have": 3}),
     "2026-11-06 09:00 同時にいる人数が足りない(必要4・実際3)"),
])
def test_violation_sentences_for_validate_detail_keys(v, expected):
    """app/validate 가 넣는 detail 키(11/12 시점)를 읽을 수 있는 말로. 모르는 키는 key=value."""
    assert violation_text(v) == expected


@pytest.mark.req("T-1")
def test_rule_violation_sentence_names_the_rule(azuki_ctx):
    ctx = dataclasses.replace(azuki_ctx, template_rules=_rules())
    v = Violation("RULE_VIOLATION", "required", date(2026, 11, 1), 3, {"rule_key": "r1"})
    assert violation_text(v, ctx) == "2026-11-01 ID3 店長の指示に反している(店長の指示 r1: ID3は日曜日に入れない)"


@pytest.mark.req("M-2")
def test_instruction_request_lists_ids_only(azuki_ctx):
    system, messages = build_instruction_request(azuki_ctx, "3番は日曜不可")
    user = messages[0]["content"]
    assert "社員: 1, 2, 3, 4, 16" in user and "【店長の指示文】\n3番は日曜不可" in user
    assert re.search(r"not_available.*max_consecutive.*min_headcount", system[0]["text"], re.S)
