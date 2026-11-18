"""LLM 출력 형식 고정 (하네스 C2) + 코드로 다시 검사.

- 출력 약속: assignments(staff_id・date・start・end, 30분 단위) + shortages(참고용) + notes (V9_ko 484).
- JSON 스키마는 구조화 출력이 받는 단순한 모양만 쓴다 (object/array/string/integer/enum/anyOf/null,
  모든 object 에 additionalProperties:false). minimum · maxLength · pattern 같은 제약은 넣지 않는다.
- 11/08 mix1_55(통과한 유일한 측정 구성)와 같게 staff_id · date · 시각을 enum 으로 묶는다 (USE_ENUMS).
- 구조화 출력이 있어도 코드가 한 번 더 본다: 형식이 틀린 행은 버리고 몇 행 버렸는지 notes 에 남긴다 (가정 신규 — 보고서).
- 0행이면 ok=False, error="empty" → 그래프가 재편성 1회로 센다 (F-4).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from datetime import date, time

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from app.domain import (
    SLOT_MINUTES,
    Assignment,
    ComposeResult,
    InstructionResult,
    PeriodContext,
    TemplateRule,
    parse_template_rule,
)
from app.llm.hours import slot_marks

USE_ENUMS = True  # mix1_55 와 같은 구성. False 면 staff_id/date/시각을 그냥 integer/string 으로 둔다

# 구조화 출력이 받지 않는 키워드 (claude-api 스킬 문서 「JSON Schema Limitations」). 테스트가 이 목록으로 스키마를 검사한다.
UNSUPPORTED_SCHEMA_KEYWORDS = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
    "patternProperties", "if", "then", "else", "not", "oneOf",
})

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ---------------------------------------------------------------- 편성안 스키마

def draft_schema(ctx: PeriodContext) -> dict:
    staff_ids = sorted(e.employee_id for e in ctx.employees)
    dates = [d.isoformat() for d in ctx.period.dates()]
    times = slot_marks(ctx)

    def _int(values: list[int]) -> dict:
        return {"type": "integer", "enum": values} if USE_ENUMS and values else {"type": "integer"}

    def _str(values: list[str]) -> dict:
        return {"type": "string", "enum": values} if USE_ENUMS and values else {"type": "string"}

    assignment = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"staff_id": _int(staff_ids), "date": _str(dates), "start": _str(times), "end": _str(times)},
        "required": ["staff_id", "date", "start", "end"],
    }
    shortage = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "date": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
            "need": {"type": "integer"}, "have": {"type": "integer"}, "reason": {"type": "string"},
        },
        "required": ["date", "start", "end", "need", "have", "reason"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "assignments": {"type": "array", "items": assignment},
            "shortages": {"type": "array", "items": shortage},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["assignments", "shortages", "notes"],
    }


def unsupported_keywords(schema: object, path: str = "$") -> list[str]:
    """스키마 안에서 구조화 출력이 받지 않는 키워드 · additionalProperties 누락을 찾는다 (빈 목록 = 문제 없음)."""
    found: list[str] = []
    if isinstance(schema, dict):
        for k, v in schema.items():
            if k in UNSUPPORTED_SCHEMA_KEYWORDS:
                found.append(f"{path}.{k}")
            if k == "additionalProperties" and v is not False:
                found.append(f"{path}.additionalProperties={v!r}")
        if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
            found.append(f"{path}: object without additionalProperties:false")
        for k, v in schema.items():
            if k == "properties" and isinstance(v, dict):
                for pk, pv in v.items():
                    found += unsupported_keywords(pv, f"{path}.properties.{pk}")
            elif isinstance(v, (dict, list)):
                found += unsupported_keywords(v, f"{path}.{k}")
    elif isinstance(schema, list):
        for i, v in enumerate(schema):
            found += unsupported_keywords(v, f"{path}[{i}]")
    return found


# ---------------------------------------------------------------- 편성안 다시 검사

class _RawAssignment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    staff_id: StrictInt
    date: str
    start: str
    end: str


class _RawShortage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    date: str
    start: str
    end: str
    need: StrictInt | None = None
    have: StrictInt | None = None
    reason: str = ""


_DROP_LABEL = {
    "shape": "形が違う",
    "unknown_staff": "不明なID",
    "out_of_period": "期間外の日付",
    "bad_time": "30分単位の時刻でない",
    "end_not_after_start": "終了が開始より後でない",
}


def _slot_time(s: str) -> time | None:
    m = _HHMM.match(s)
    if not m:
        return None
    t = time(int(m.group(1)), int(m.group(2)))
    return t if t.minute % SLOT_MINUTES == 0 else None


def _iso_date(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def parse_draft(ctx: PeriodContext, data: object) -> ComposeResult:
    """LLM 이 낸 JSON(dict) → ComposeResult. 틀린 행은 버리고 notes 에 「몇 행 · 왜」를 남긴다.

    버리지 않는 것: 같은 사람 같은 날 2행 · 영업시간 밖 · 신청 밖 — 이것들은 validate 가 위반으로 잡아야 한다
    (여기서 지우면 DOUBLE_BOOKING 등 V-8 이 가려진다).
    """
    if not isinstance(data, dict) or not isinstance(data.get("assignments"), list):
        return ComposeResult(ok=False, error="parse_error")

    staff_ids = {e.employee_id for e in ctx.employees}
    period_dates = set(ctx.period.dates())
    dropped: Counter[str] = Counter()
    out: list[Assignment] = []
    for row in data["assignments"]:
        try:
            r = _RawAssignment.model_validate(row)
        except ValidationError:
            dropped["shape"] += 1
            continue
        if r.staff_id not in staff_ids:
            dropped["unknown_staff"] += 1
            continue
        d = _iso_date(r.date)
        if d is None or d not in period_dates:
            dropped["out_of_period"] += 1
            continue
        s, e = _slot_time(r.start), _slot_time(r.end)
        if s is None or e is None:
            dropped["bad_time"] += 1
            continue
        if e <= s:
            dropped["end_not_after_start"] += 1
            continue
        out.append(Assignment(employee_id=r.staff_id, work_date=d, kind="work", start_time=s, end_time=e))

    shortages: list[dict] = []
    for row in data.get("shortages") or []:
        try:
            sh = _RawShortage.model_validate(row)
        except ValidationError:
            continue  # 참고용(F-14)이라 버린 수는 세지 않는다
        # 화면 계약(API_CONTRACT ReviewWait.shortages)의 키 이름으로 바꿔 둔다
        shortages.append({"work_date": sh.date, "start_time": sh.start, "end_time": sh.end,
                          "need": sh.need, "have": sh.have, "reason": sh.reason})

    notes = [n for n in (data.get("notes") or []) if isinstance(n, str) and n.strip()]
    n_dropped = sum(dropped.values())
    if n_dropped:
        detail = "・".join(f"{_DROP_LABEL[k]} {v}行" for k, v in sorted(dropped.items()))
        notes.append(f"[コード確認] 形式に合わない行を{n_dropped}行除いた({detail})")

    usage = {"n_rows_in": len(data["assignments"]), "n_dropped": n_dropped, "dropped": dict(dropped)}
    if not out:
        return ComposeResult(ok=False, shortages=shortages, notes=notes, error="empty", usage=usage)
    return ComposeResult(ok=True, assignments=out, shortages=shortages, notes=notes, usage=usage)


def parse_draft_text(ctx: PeriodContext, text: str) -> ComposeResult:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return ComposeResult(ok=False, error="parse_error")
    return parse_draft(ctx, data)


# ---------------------------------------------------------------- 점장 지시 → 틀 규칙 (M-2)

def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


# params 는 틀마다 모양이 달라서, 모든 칸을 null 허용으로 펼친 한 모양을 쓴다. null 칸은 코드가 지운 뒤
# parse_template_rule(extra="forbid")에 넘긴다 → 틀에 없는 칸에 값이 있으면 거부된다 (T-4).
_PARAM_FIELDS = {
    "staff_id": {"type": "integer"},
    "weekday": {"type": "integer"},
    "date": {"type": "string"},
    "shift": {"type": "string", "enum": ["any", "late"]},
    "limit": {"type": "integer"},
    "count": {"type": "integer"},
    "start": {"type": "string"},
    "end": {"type": "string"},
}


def instruction_schema() -> dict:
    params = {
        "type": "object",
        "additionalProperties": False,
        "properties": {k: _nullable(v) for k, v in _PARAM_FIELDS.items()},
        "required": list(_PARAM_FIELDS),
    }
    rule = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "template_key": {"type": "string", "enum": ["not_available", "max_consecutive", "min_headcount"]},
            "params": params,
            "source_text": {"type": "string"},
        },
        "required": ["template_key", "params", "source_text"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"rules": {"type": "array", "items": rule},
                       "free_text": {"type": "array", "items": {"type": "string"}}},
        "required": ["rules", "free_text"],
    }


def _short(e: Exception) -> str:
    s = " ".join(str(e).split())
    return s if len(s) <= 300 else s[:297] + "..."


def finalize_rules(ctx: PeriodContext, candidates: Iterable[object], free_text: Iterable[object]) -> InstructionResult:
    """후보(LLM · 가짜 파서 공통) → 틀 검사(parse_template_rule, V9_ko 1170) → rules / rejected. rule_key 는 r1, r2 …

    (가정 신규) 틀 검사에 더해, 이 기간에 없는 staff_id · 기간 밖 date 도 거부한다 — 지켜도 의미가 없는 규칙을
    「보장」으로 보여 주지 않기 위해 (보수적).
    """
    staff_ids = {e.employee_id for e in ctx.employees}
    period_dates = set(ctx.period.dates())
    rules: list[TemplateRule] = []
    rejected: list[dict] = []
    for c in candidates:
        raw = c if isinstance(c, dict) else {"value": c}
        try:
            if not isinstance(c, dict):
                raise ValueError("rule must be an object")
            tk = c.get("template_key")
            params = c.get("params")
            if not isinstance(tk, str):
                raise ValueError("template_key missing")
            if not isinstance(params, dict):
                raise ValueError("params must be an object")
            params = {k: v for k, v in params.items() if v is not None}
            source = c.get("source_text") if isinstance(c.get("source_text"), str) else ""
            rule = parse_template_rule(f"r{len(rules) + 1}", tk, params, source)
            sid = getattr(rule.params, "staff_id", None)
            if sid is not None and sid not in staff_ids:
                raise ValueError(f"unknown staff_id: {sid}")
            d = getattr(rule.params, "date", None)
            if d is not None and d not in period_dates:
                raise ValueError(f"date outside period: {d}")
        except (ValueError, TypeError) as e:  # pydantic ValidationError 도 ValueError
            rejected.append({"raw": raw, "reason": _short(e)})
            continue
        rules.append(rule)
    free = [t.strip() for t in free_text if isinstance(t, str) and t.strip()]
    return InstructionResult(rules=rules, free_text=free, rejected=rejected)
