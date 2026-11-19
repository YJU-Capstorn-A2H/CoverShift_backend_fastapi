"""입구 API (D3, V9_ko 508). 실행: uv run uvicorn app.api_seung.main:app --host 127.0.0.1 --port 8010

관리자 Web · 직원 제출 페이지 · 답장 버튼의 요청을 받아 DB 를 읽고 쓰고, 편성은 러너(app/runner.py)에 넘긴다.
"""
from __future__ import annotations

from fastapi import FastAPI, Request

from . import errors
from .deps import AlreadyStarted, Busy, NotWaiting, StaleDraft
from .routes_auth import router as auth_router
from .routes_control import router as control_router
from .routes_instructions import router as instructions_router
from .routes_settings import router as settings_router
from .routes_submit import router as submit_router


def create_app() -> FastAPI:
    app = FastAPI(title="CoverShift MVP 입구 API", version="0.1.0")
    errors.install(app)

    # 라우트가 직접 잡지 못한 러너 예외도 같은 모양으로 (A-15, A-19)
    @app.exception_handler(Busy)
    async def _busy(_: Request, e: Exception):
        return errors.error_response(409, "BUSY", errors.BUSY_MESSAGE)

    @app.exception_handler(NotWaiting)
    async def _not_waiting(_: Request, e: Exception):
        return errors.error_response(409, "NOT_WAITING", "지금은 기다리는 입력이 없습니다")

    @app.exception_handler(StaleDraft)
    async def _stale(_: Request, e: Exception):
        return errors.error_response(409, "STALE_DRAFT", "화면의 편성안이 최신이 아닙니다")

    @app.exception_handler(AlreadyStarted)
    async def _already(_: Request, e: Exception):
        return errors.error_response(409, "ALREADY_STARTED", "이 기간은 이미 편성을 시작했습니다")

    for r in (auth_router, settings_router, submit_router, instructions_router, control_router):
        app.include_router(r)
    return app


app = create_app()
