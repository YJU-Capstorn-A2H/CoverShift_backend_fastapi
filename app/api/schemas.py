"""요청 본문 모양 (API_CONTRACT). 모르는 필드는 받지 않는다(extra="forbid") — 특히 store_id 를 몰래 받지 않게 (A-5).

DB 에 CHECK 가 있는 값(요일 범위 · 인원 하한 · weekly_days ↔ fixed_weekdays 등)은 여기서 다시 막지 않는다.
DB 가 최종 판정 → 처리기가 422 CHECK_VIOLATION (V9_ko 178). 여기서는 DB 가 못 보는 것만 본다(30분 단위 · 형식).
"""
from __future__ import annotations

from datetime import date, time
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


def check_slot(v: time | None) -> time | None:
    """30분 단위 (V9_ko 165, 447)."""
    if v is not None and (v.minute not in (0, 30) or v.second or v.microsecond):
        raise ValueError("시각은 30분 단위(HH:00 / HH:30)여야 합니다")
    return v


SlotTime = Annotated[time, AfterValidator(check_slot)]


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- 인증

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterIn(In):
    invite_code: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    # (가정 신규) 비밀번호 규칙이 명세에 없다(V9_ko 432 「미정」). 8자 이상 · bcrypt 한계 72바이트 이하만 받는다
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def _bcrypt_limit(cls, v: str) -> str:
        if len(v.encode()) > 72:
            raise ValueError("비밀번호는 72바이트 이하여야 합니다")
        return v


class LoginIn(In):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------- 설정 CRUD

class StorePatch(In):
    name: str = Field(min_length=1, max_length=100)


class TimeRuleIn(In):
    weekday: int  # 1=월 … 7=일. 범위는 DB CHECK
    start_time: SlotTime
    end_time: SlotTime
    min_headcount: int  # 휴게 포함 필요 인원 (M-8, V9_ko 240)
    min_qualified: int = 0


class TimeRulePatch(In):
    weekday: int | None = None
    start_time: SlotTime | None = None
    end_time: SlotTime | None = None
    min_headcount: int | None = None
    min_qualified: int | None = None


class CopyIn(In):
    from_weekday: int = Field(ge=1, le=7)
    to_weekdays: list[Annotated[int, Field(ge=1, le=7)]] = Field(min_length=1, max_length=7)


def _weekdays(v: list[int] | None) -> list[int] | None:
    if v is None:
        return v
    if len(set(v)) != len(v):
        raise ValueError("fixed_weekdays 에 같은 요일이 두 번 있습니다")
    return sorted(v)


Weekdays = Annotated[list[int], Field(min_length=1, max_length=7), AfterValidator(_weekdays)]


class EmployeeIn(In):
    name: str = Field(min_length=1, max_length=100)
    is_regular: bool
    has_qualification: bool = False
    closing_ok: bool = False
    weekly_days: int | None = None
    fixed_weekdays: Weekdays | None = None  # 고정 없음 = null (빈 배열은 받지 않는다)


class EmployeePatch(In):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_regular: bool | None = None
    has_qualification: bool | None = None
    closing_ok: bool | None = None
    weekly_days: int | None = None
    fixed_weekdays: Weekdays | None = None


class TaskIn(In):
    name: str = Field(min_length=1, max_length=100)
    sort_order: int = 0


class TaskPatch(In):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    sort_order: int | None = None


class PeriodIn(In):
    target_month: date  # 그 달 1일. 1일이 아니면 DB CHECK → 422
    submission_deadline: AwareDatetime  # 시간대 없는 시점은 받지 않는다 (API_CONTRACT 「시점 ISO 8601」)
    designated_off_days: int
    off_request_limit: int | None = None  # null = 제한 없음 (V9_ko 192)
    work_request_limit: int | None = None


class PeriodPatch(In):
    submission_deadline: AwareDatetime | None = None
    designated_off_days: int | None = None
    off_request_limit: int | None = None
    work_request_limit: int | None = None


# ---------------------------------------------------------------- 제출 · LINE

class AvailabilityItemIn(In):
    """종류 · 시각 규칙은 라우트가 보고 422 INVALID_ITEM 으로 돌린다 (API_CONTRACT 의 코드)."""

    work_date: date
    kind: str
    start_time: time | None = None
    end_time: time | None = None


class AvailabilityPut(In):
    items: list[AvailabilityItemIn] = Field(min_length=1, max_length=62)


class LinkIn(In):
    link_token: str = Field(min_length=1, max_length=200)
    line_user_id: str = Field(min_length=1, max_length=100)


class ReplyIn(In):
    contact_id: int
    line_user_id: str = Field(min_length=1, max_length=100)
    answer: Literal["yes", "no"]


# ---------------------------------------------------------------- 점장 지시 → 틀 규칙 (M-2)

class InstructionIn(In):
    text: str = Field(min_length=1, max_length=4000)


class RuleIn(In):
    rule_key: str = Field(min_length=1, max_length=50)
    template_key: str  # 틀 이름 검사는 parse_template_rule 이 한다 → 422 INVALID_RULE
    params: dict
    source_text: str = Field(default="", max_length=2000)


class ConfirmIn(In):
    text: str = Field(min_length=1, max_length=4000)
    rules: list[RuleIn] = Field(max_length=50)
    free_text: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(default_factory=list,
                                                                                 max_length=50)


# ---------------------------------------------------------------- 편성 제어 (G1, F-7)

class AssignmentIn(In):
    employee_id: int
    work_date: date
    kind: Literal["work", "paid_leave"]
    start_time: SlotTime | None = None
    end_time: SlotTime | None = None
    break_start: time | None = None  # 휴게는 기록만 (V9_ko 351)
    break_end: time | None = None

    @model_validator(mode="after")
    def _shape(self):
        # shift_assignments 의 CHECK 와 같은 규칙. 그래프가 저장하기 전에 입구에서 먼저 돌려준다
        if self.kind == "work" and (self.start_time is None or self.end_time is None):
            raise ValueError("work 는 start_time · end_time 이 필요합니다")
        if self.kind == "paid_leave" and (self.start_time is not None or self.end_time is not None):
            raise ValueError("paid_leave 는 시각이 없어야 합니다")
        if self.start_time is not None and self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time 은 start_time 보다 늦어야 합니다")
        if (self.break_start is None) != (self.break_end is None):
            raise ValueError("break_start · break_end 는 둘 다 있거나 둘 다 없어야 합니다")
        if self.break_start is not None:
            if self.kind != "work" or not (self.start_time <= self.break_start < self.break_end <= self.end_time):
                raise ValueError("휴게는 근무 시간 안이어야 합니다")
        return self


class ApprovedIn(In):
    decision: Literal["approved"]
    draft_id: int
    waived_keys: list[str] = Field(default_factory=list, max_length=500)


class RedraftIn(In):
    decision: Literal["redraft_requested"]
    draft_id: int
    instruction: str = Field(max_length=4000)


class EditIn(In):
    decision: Literal["edit_saved"]
    draft_id: int
    assignments: list[AssignmentIn] = Field(min_length=1, max_length=5000)


ReviewBody = Annotated[ApprovedIn | RedraftIn | EditIn, Field(discriminator="decision")]
