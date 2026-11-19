"""설정값. 전부 환경 변수에서 읽는다 (값은 .env, 이름은 .env.example). 키·비밀번호는 코드에 쓰지 않는다 (V9_ko 511)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


LINE_MODES = ("stub", "line")


class ConfigError(RuntimeError):
    """설정이 틀려 시작할 수 없다. 문장에 값(시크릿)은 싣지 않고 이름만 싣는다."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    jwt_ttl_minutes: int
    dev_skip_auth: bool  # 개발 중 로그인 건너뛰기 (V9_ko 53, 511). 시연 메인 흐름에서는 끈다
    llm_mode: str  # "fake" | "real"
    llm_model: str  # 정함: claude-sonnet-5-5 (V9_ko 482, M8)
    llm_timeout_sec: int  # 정함(임시): 600초 (11/12 승혁)
    llm_max_tokens: int
    auto_retry_limit: int  # 정함: 자동 3회 (V9_ko 479, 487)
    manager_request_limit: int  # 정함: 점장 요청 2회
    enable_surplus: bool  # G3 — 기본 꺼 둠
    enable_half_leave_check: bool  # G5
    enable_regular_availability_warning: bool  # G5
    enable_contact: bool  # M-6 — 맨 마지막, 기본 꺼 둠
    runner: str  # "local" | "aegra"
    aegra_url: str
    public_base_url: str  # 제출 링크·LINE 연결 링크를 만들 때의 앞부분
    line_mode: str  # "stub" = 보내지 않고 기록만(기본) | "line" = 진짜 LINE Messaging API
    line_channel_secret: str  # 웹훅 서명 검증 키 (LINE 문서 · 웹훅 서명)
    line_channel_access_token: str  # push 의 Bearer 토큰
    line_official_account_id: str  # 공식 계정의 LINE ID (@...). 연결 링크(oaMessage)를 만든다. 비면 스텁 화면 링크


def _check_line(s: Settings) -> None:
    """line 모드인데 값이 비면 시작할 때 멈춘다: 빈 시크릿은 누구나 만들 수 있는 서명을 받게 하고, 빈 토큰은 push 가 전부 401,
    빈 공식 계정 ID 는 연결 링크가 스텁 화면(line 모드에서는 404 인 POST /api/line/link)을 가리키게 한다."""
    if s.line_mode not in LINE_MODES:
        raise ConfigError(f"LINE_MODE 는 {' | '.join(LINE_MODES)} 중 하나여야 합니다")
    if s.line_mode != "line":
        return
    missing = [name for name, value in (("LINE_CHANNEL_SECRET", s.line_channel_secret),
                                        ("LINE_CHANNEL_ACCESS_TOKEN", s.line_channel_access_token),
                                        ("LINE_OFFICIAL_ACCOUNT_ID", s.line_official_account_id)) if not value]
    if missing:
        raise ConfigError(f"LINE_MODE=line 인데 {' · '.join(missing)} 가 비어 있습니다")


def get_settings() -> Settings:
    """부를 때마다 환경 변수에서 읽는다. LINE 설정이 틀리면 ConfigError — API(create_app) · 발송기(main) · Aegra 가 읽는 그래프
    (app/graph/aegra_entry.py)가 시작할 때 한 번 부른다."""
    e = os.environ.get
    s = Settings(
        database_url=e("DATABASE_URL", "postgresql://mvp:mvp@127.0.0.1:5443/mvp"),
        jwt_secret=e("JWT_SECRET", ""),  # 기본값 없음 (리뷰 A #13). 비면 API 가 서명·검증을 거부한다
        jwt_ttl_minutes=int(e("JWT_TTL_MINUTES", "60")),
        dev_skip_auth=_bool("DEV_SKIP_AUTH", False),
        llm_mode=e("LLM_MODE", "fake"),
        llm_model=e("LLM_MODEL", "claude-sonnet-5-5"),
        llm_timeout_sec=int(e("LLM_TIMEOUT_SEC", "600")),
        llm_max_tokens=int(e("LLM_MAX_TOKENS", "64000")),
        auto_retry_limit=int(e("AUTO_RETRY_LIMIT", "3")),
        manager_request_limit=int(e("MANAGER_REQUEST_LIMIT", "2")),
        enable_surplus=_bool("ENABLE_SURPLUS", False),
        enable_half_leave_check=_bool("ENABLE_HALF_LEAVE_CHECK", False),
        enable_regular_availability_warning=_bool("ENABLE_REGULAR_AVAILABILITY_WARNING", False),
        enable_contact=_bool("ENABLE_CONTACT", False),
        runner=e("RUNNER", "local"),
        aegra_url=e("AEGRA_URL", "http://127.0.0.1:2026"),
        public_base_url=e("PUBLIC_BASE_URL", "http://127.0.0.1:5173"),
        line_mode=e("LINE_MODE", "stub").strip().lower(),
        line_channel_secret=e("LINE_CHANNEL_SECRET", "").strip(),
        line_channel_access_token=e("LINE_CHANNEL_ACCESS_TOKEN", "").strip(),
        line_official_account_id=e("LINE_OFFICIAL_ACCOUNT_ID", "").strip(),
    )
    _check_line(s)
    return s
