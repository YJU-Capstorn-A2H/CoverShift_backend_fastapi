"""설정값. 전부 환경 변수에서 읽는다 (값은 .env, 이름은 .env.example). 키·비밀번호는 코드에 쓰지 않는다 (V9_ko 511)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


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


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
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
    )
