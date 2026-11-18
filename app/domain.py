"""공유 계약 (모든 모듈이 이 파일의 형만 주고받는다).

- 이 파일은 메인 세션이 소유한다. 서브에이전트는 고치지 않는다. 필요하면 보고서에 「계약 변경 요청」으로 쓴다.
- 이름(employees.name)은 여기에 없다. LLM·검증·그래프는 ID 만 본다 (명세 2-6 ④ 「실명 없이 ID만」, 9-7).
- 근거 줄 번호는 spec/V9_ko.txt 기준.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SLOT_MINUTES = 30  # 30분 단위 (V9_ko 165, 447)

AvailabilityKind = Literal["available", "off", "paid_leave", "half_leave"]  # V9_ko 290-317
AssignmentKind = Literal["work", "paid_leave"]  # V9_ko 344-360
Severity = Literal["required", "warning"]  # required = 재편성 계기 / warning = 점장에게 알림만 (V9_ko 466-479)
TemplateKey = Literal["not_available", "max_consecutive", "min_headcount"]  # MVP 틀 3개 (V9_ko 478)
Decision = Literal["approved", "redraft_requested", "edit_saved"]  # V9_ko 110
DraftResult = Literal["valid", "retry", "limit_reached", "draft_empty"]  # V9_ko 109


# ---------------------------------------------------------------- 입력 (DB 에서 읽은 값)

@dataclass(frozen=True)
class Employee:
    """employees 행에서 name·LINE 열을 뺀 것 (V9_ko 249-263)."""

    employee_id: int
    is_regular: bool  # true=정직원(사원), false=파트・알바
    has_qualification: bool  # 등록판매자
    closing_ok: bool  # 폐점(그 요일 마지막 시각)까지 남아도 되는가
    weekly_days: int | None = None
    fixed_weekdays: tuple[int, ...] | None = None  # 1=월 ... 7=일


@dataclass(frozen=True)
class Availability:
    """availability_submissions 행 (V9_ko 290-317). 행 없음 = 사원: 희망 없음(배치 가능) / 파트・알바: 불가."""

    employee_id: int
    work_date: date
    kind: AvailabilityKind
    start_time: time | None = None
    end_time: time | None = None


@dataclass(frozen=True)
class TimeRule:
    """store_time_rules 행 (V9_ko 234-245). 요일×시간대 1행."""

    rule_id: int
    weekday: int  # 1=월 ... 7=일 (date.isoweekday() 와 같음)
    start_time: time
    end_time: time
    min_headcount: int  # 휴게 포함 필요 인원 — 설정값은 「경고」 (V9_ko 476, 514)
    min_qualified: int = 0  # 시간대별 필요한 자격자 수 — 「반드시・예외 불가」 (V9_ko 469)


@dataclass(frozen=True)
class Period:
    """shift_periods 행 (V9_ko 267-277)."""

    period_id: int
    store_id: int
    target_month: date  # 그 달 1일
    designated_off_days: int  # 사원 1명당 월 공휴일수
    off_request_limit: int | None = None
    work_request_limit: int | None = None

    def dates(self) -> list[date]:
        d = self.target_month
        out: list[date] = []
        while d.month == self.target_month.month:
            out.append(d)
            d += timedelta(days=1)
        return out


# ---------------------------------------------------------------- 틀 규칙 (M-2, V9_ko 478 · 5-2 965-975)
# params 모양은 명세에 min_headcount 가 없고 max_consecutive 의 "late" 정의도 없다 → ASSUMPTIONS G11·G12.

class NotAvailableParams(BaseModel):
    """「이 사람은 일요일 불가」 (V9_ko 970). weekday 또는 date 중 하나."""

    model_config = ConfigDict(extra="forbid")
    staff_id: int
    weekday: int | None = Field(default=None, ge=1, le=7)
    date: dt.date | None = None

    @model_validator(mode="after")
    def _one_of(self):
        if (self.weekday is None) == (self.date is None):
            raise ValueError("weekday 와 date 중 정확히 하나")
        return self


class MaxConsecutiveParams(BaseModel):
    """「늦은 근무를 2번 연속으로 넣지 마」 (V9_ko 967). (가정 G11) shift: any=출근일 / late=그 날 폐점 시각에 끝나는 근무."""

    model_config = ConfigDict(extra="forbid")
    shift: Literal["any", "late"] = "any"
    limit: int = Field(ge=1, le=31)
    staff_id: int | None = None  # None = 전원


class MinHeadcountParams(BaseModel):
    """(가정 G12) 점장 지시의 최소 인원. 지시가 있는 칸은 설정값을 덮어쓰고 「보장」(V9_ko 476, 514)."""

    model_config = ConfigDict(extra="forbid")
    count: int = Field(ge=1, le=99)
    start: time
    end: time
    weekday: int | None = Field(default=None, ge=1, le=7)  # 둘 다 None = 매일
    date: dt.date | None = None

    @field_validator("start", "end")
    @classmethod
    def _slot(cls, v: time) -> time:
        if v.minute not in (0, 30) or v.second:
            raise ValueError("30분 단위")
        return v

    @model_validator(mode="after")
    def _order(self):
        if self.end <= self.start:
            raise ValueError("end > start")
        if self.weekday is not None and self.date is not None:
            raise ValueError("weekday 와 date 는 함께 쓰지 않는다")
        return self


TEMPLATE_PARAMS: dict[str, type[BaseModel]] = {
    "not_available": NotAvailableParams,
    "max_consecutive": MaxConsecutiveParams,
    "min_headcount": MinHeadcountParams,
}


@dataclass(frozen=True)
class TemplateRule:
    rule_key: str  # 같은 기간 안에서 유일 (예 "r1")
    template_key: TemplateKey
    params: BaseModel  # TEMPLATE_PARAMS[template_key] 의 인스턴스
    source_text: str  # 점장 원문


def parse_template_rule(rule_key: str, template_key: str, params: dict, source_text: str) -> TemplateRule:
    """틀 검사(C4) 입구. 틀 이름이 없거나 params 가 틀리면 ValueError (V9_ko 1170 「틀 없는 틀 규칙은 거부」)."""
    if template_key not in TEMPLATE_PARAMS:
        raise ValueError(f"unknown template_key: {template_key}")
    model = TEMPLATE_PARAMS[template_key].model_validate(params)
    return TemplateRule(rule_key=rule_key, template_key=template_key, params=model, source_text=source_text)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ValidateOptions:
    """제안 상태(기준 문서 미반영) 규칙은 기본으로 꺼 둔다 (승혁 11/12 계획 · ASSUMPTIONS G3·G5)."""

    enable_surplus: bool = False  # V-10 余り (G3)
    enable_half_leave_check: bool = False  # V-6 HALF_LEAVE_MISMATCH (G5)
    enable_regular_availability_warning: bool = False  # V-7 사원 available 시각 (G5)


@dataclass(frozen=True)
class PeriodContext:
    """검증·LLM 입력 전부. 이름 없음."""

    period: Period
    employees: tuple[Employee, ...]
    availability: tuple[Availability, ...]
    time_rules: tuple[TimeRule, ...]
    template_rules: tuple[TemplateRule, ...] = ()
    free_text_guidelines: tuple[str, ...] = ()  # 틀에 안 맞는 점장 말 — 프롬프트에 그대로, 화면에 「참고・보장 없음」(V9_ko 478)
    options: ValidateOptions = field(default_factory=ValidateOptions)


# ---------------------------------------------------------------- 출력

@dataclass(frozen=True)
class Assignment:
    """shift_assignments 행에서 draft_id 를 뺀 것 (V9_ko 344-360)."""

    employee_id: int
    work_date: date
    kind: AssignmentKind = "work"
    start_time: time | None = None
    end_time: time | None = None
    break_start: time | None = None  # 기록만. validate 는 보지 않는다 (V9_ko 351)
    break_end: time | None = None


# 예외 허용 불가 코드 (V9_ko 469, 475, 479 「자격 없음・중복 배치는 예외 불가」)
NON_WAIVABLE_CODES = frozenset({"QUAL_MISSING", "DOUBLE_BOOKING"})


@dataclass(frozen=True)
class Violation:
    check_code: str  # REQUIREMENTS.md 의 V-n / T-n 표에 있는 코드만
    severity: Severity
    work_date: date | None = None
    employee_id: int | None = None
    detail: dict = field(default_factory=dict)  # 예 {"time":"13:00","need":2,"have":1}. 값은 JSON 으로 바꿀 수 있는 것만

    @property
    def waivable(self) -> bool:
        return self.check_code not in NON_WAIVABLE_CODES

    @property
    def key(self) -> str:
        """예외 허용(waive)의 식별자. 같은 위반이면 같은 문자열 (C3 묶기에도 쓴다)."""
        t = self.detail.get("time") or self.detail.get("start") or ""
        r = self.detail.get("rule_key") or ""
        return f"{self.check_code}|{self.work_date or ''}|{self.employee_id or ''}|{t}|{r}"


@dataclass(frozen=True)
class ViolationGroup:
    """C3: 같은 위반(key 기준)을 묶고 몇 회차째인지 표시 (V9_ko 486)."""

    key: str
    check_code: str
    severity: Severity
    rounds: tuple[int, ...]  # 이 위반이 나온 회차 (1부터)
    example: Violation


@dataclass
class ComposeResult:
    """LLM 편성 1회의 결과 (V9_ko 484). ok=False 는 「초안 0건」 → 재편성 1회로 센다 (11/12 승혁)."""

    ok: bool
    assignments: list[Assignment] = field(default_factory=list)  # kind='work' 만. 유급 행은 코드가 희망에서 채운다
    shortages: list[dict] = field(default_factory=list)  # 화면 참고용. 코드 검사는 안 봄
    notes: list[str] = field(default_factory=list)
    error: str | None = None  # timeout / max_tokens / refusal / parse_error / empty
    usage: dict = field(default_factory=dict)  # {"seconds":..,"input_tokens":..,"output_tokens":..,"model":..}


@dataclass
class InstructionResult:
    """M-2: 점장 지시 → 틀 규칙 (그래프 밖, 1회). 점장이 확인한 뒤에야 저장된다."""

    rules: list[TemplateRule]
    free_text: list[str]  # 틀에 안 맞는 말
    rejected: list[dict] = field(default_factory=list)  # LLM 이 냈지만 틀 검사(parse_template_rule)에서 떨어진 것
