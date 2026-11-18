"""그래프가 바깥에 기대는 것들 (담당: graph 에이전트).

시험에서는 작은 가짜로 바꿔 끼운다 (default_deps(composer=..., validate_draft=...)).
다른 에이전트가 같은 시간에 쓰는 모듈(app.validate, app.llm)은 **부를 때** 이름을 찾는다
→ 지금은 NotImplementedError 를 내는 스텁이어도 그래프를 만들고 import 하는 데는 문제가 없다 (Aegra 시작 포함).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

log = logging.getLogger("graph.deps")


def _staff_info_default() -> Callable[[int], dict]:
    """M-7・G7: 그래프의 collect 는 MCP 로 직원 정보를 읽는다 (10/25 승혁 「메인은 collect가 MCP 부르고」).
    MCP_MODE=stdio 이고 app.mcp_client 가 있으면 MCP 클라이언트, 아니면 같은 함수를 직접 부른다(같은 DB 롤로 이름 제외)."""
    from app.mcp_tools import get_staff_info

    if os.environ.get("MCP_MODE", "").strip().lower() == "stdio":
        try:
            from app.mcp_client import call_staff_info  # type: ignore[import-not-found]
            return call_staff_info
        except ImportError:
            log.warning("MCP_MODE=stdio 인데 app.mcp_client 가 없다 → app.mcp_tools.get_staff_info 를 직접 부른다")
    return get_staff_info


def _validate_fn(name: str) -> Callable:
    def call(*a, **kw):
        import app.validate as v
        return getattr(v, name)(*a, **kw)
    call.__name__ = name
    return call


def _composer_default():
    from app.llm import get_composer
    return get_composer()


def _connect_default():
    from app.db import connect
    return connect()


def _send_default(conn, **kw) -> bool:
    from app.line_stub import send
    return send(conn, **kw)


def _settings_default():
    from app.config import get_settings
    return get_settings()


@dataclass
class Deps:
    composer_factory: Callable[[], Any] = _composer_default  # () -> app.llm.Composer
    validate_draft: Callable = field(default_factory=lambda: _validate_fn("validate_draft"))
    impossible_slots: Callable = field(default_factory=lambda: _validate_fn("impossible_slots"))
    group_history: Callable = field(default_factory=lambda: _validate_fn("group_history"))
    blocking: Callable = field(default_factory=lambda: _validate_fn("blocking"))
    staff_info: Callable[[int], dict] | None = None  # None → _staff_info_default() 를 처음 부를 때 고른다
    connect: Callable = _connect_default  # () -> 컨텍스트 매니저 (with 가 끝나면 커밋)
    send: Callable = _send_default  # app.line_stub.send 와 같은 서명
    settings_factory: Callable[[], Any] = _settings_default
    _composer: Any = field(default=None, init=False, repr=False)

    @property
    def composer(self):
        if self._composer is None:
            self._composer = self.composer_factory()
        return self._composer

    @property
    def settings(self):
        # 부를 때마다 환경 변수에서 읽는다 (시험이 monkeypatch 로 바꿀 수 있게). 값이 몇 개 안 되어 비용은 작다.
        return self.settings_factory()

    def get_staff_info(self, period_id: int) -> dict:
        fn = self.staff_info or _staff_info_default()
        return fn(period_id)


def default_deps(*, composer=None, settings=None, **overrides) -> Deps:
    """기본 Deps. composer= / settings= 는 객체를 바로 받는다 (시험용 지름길)."""
    d = Deps()
    if composer is not None:
        overrides["composer_factory"] = lambda: composer
    if settings is not None:
        overrides["settings_factory"] = lambda: settings
    return replace(d, **overrides) if overrides else d
