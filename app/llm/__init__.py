"""LLM 경로 — 담당: llm 에이전트. 서명은 바꾸지 않는다 (graph·api 가 이 이름으로 부른다).

- Composer: 편성안 1회(compose) · 점장 지시 → 틀 규칙(instruction_to_rules, 그래프 밖 1회 · G18).
- get_composer(): LLM_MODE=fake → FakeComposer (네트워크 없음), real → AnthropicComposer (claude-sonnet-5-5).
  그 밖의 값은 ValueError — 모르는 값일 때 조용히 진짜 API 로 가지 않게.
- 하위 모듈: prompt(문장) · schema(출력 형식 · 다시 검사) · anthropic_composer · fake · usage_log(측정 기록) · hours.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.config import get_settings
from app.domain import Assignment, ComposeResult, InstructionResult, PeriodContext, ViolationGroup


class Composer(Protocol):
    def compose(
        self,
        ctx: PeriodContext,
        *,
        previous: Sequence[Assignment] | None,
        history: Sequence[ViolationGroup],
        instruction: str | None,
    ) -> ComposeResult: ...

    def instruction_to_rules(self, ctx: PeriodContext, text: str) -> InstructionResult: ...


def get_composer() -> Composer:
    """LLM_MODE=fake → FakeComposer, real → AnthropicComposer. 부를 때마다 새로 만든다 (상태를 남기지 않음)."""
    settings = get_settings()
    if settings.llm_mode == "fake":
        from app.llm.fake import FakeComposer

        return FakeComposer()
    if settings.llm_mode == "real":
        from app.llm.anthropic_composer import AnthropicComposer

        return AnthropicComposer(settings=settings)
    raise ValueError(f"unknown LLM_MODE: {settings.llm_mode!r} (fake | real)")


__all__ = ["Composer", "get_composer"]
