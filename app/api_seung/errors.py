"""오류 응답을 한 모양으로 (A-19, V9_ko 977): 모든 오류는 {"error": {"code": "<대문자_코드>", "message": "<한국어>"}}.

- 화면 · 라우트는 ApiError 를 던지기만 한다. 모양은 여기 처리기가 정한다.
- DB 제약 위반(psycopg)은 여기서 상태 코드로 바꾼다 → 라우트가 제약을 미리 흉내 내지 않는다 (DB 가 최종 판정).
  EXCLUDE → 409 TIME_RULE_OVERLAP (V9_ko 172), CHECK → 422 CHECK_VIOLATION (V9_ko 178), UNIQUE → 409 (계약의 코드).
"""
from __future__ import annotations

import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("api")

BUSY_MESSAGE = "지금 편성을 처리하고 있습니다"  # V9_ko 509 (A-15). 화면이 이 문장을 그대로 보여 준다


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def not_found(what: str = "자원") -> ApiError:
    # 남의 점포 자원도 같은 404 (A-5, API_CONTRACT 「남의 점포 자원 = 404」) — 있는지 없는지 알려 주지 않는다
    return ApiError(404, "NOT_FOUND", f"{what}을(를) 찾을 수 없습니다")


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


# UNIQUE 제약 이름 → (코드, 문장). 이름은 PostgreSQL 기본 규칙(<표>_<열>_key)으로 db/001 에서 정해진다.
UNIQUE_CODES: dict[str, tuple[str, str]] = {
    "manager_accounts_email_key": ("EMAIL_EXISTS", "이미 등록된 이메일입니다"),
    "shift_periods_store_id_target_month_key": ("PERIOD_EXISTS", "그 달의 편성 기간이 이미 있습니다"),
    "employees_line_user_id_key": ("LINE_ALREADY_LINKED", "이 LINE 계정은 이미 다른 직원에 연결되어 있습니다"),
    # (가정 신규) 계약에 코드가 없다. UNIQUE(store_id, name) (V9_ko tasks DDL) 를 409 로 돌리는 이름만 정했다
    "tasks_store_id_name_key": ("TASK_EXISTS", "같은 이름의 업무가 이미 있습니다"),
    "one_approval_per_draft": ("ALREADY_APPROVED", "이 편성안은 이미 승인되었습니다"),  # A-16 (V9_ko 505)
    "one_confirmed_draft_per_period": ("ALREADY_CONFIRMED", "이 기간에는 이미 확정된 편성안이 있습니다"),  # A-16
}


def _constraint(e: psycopg.Error) -> str:
    return (e.diag.constraint_name or "") if e.diag else ""


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return error_response(e.status, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        errs = e.errors()
        first = errs[0] if errs else {}
        loc = ".".join(str(x) for x in first.get("loc", ()) if x not in ("body",))
        msg = f"입력값이 올바르지 않습니다: {loc} {first.get('msg', '')}".strip()
        return error_response(422, "VALIDATION_ERROR", msg)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            e.status_code, f"HTTP_{e.status_code}")
        msg = {404: "찾을 수 없습니다", 405: "허용되지 않는 메서드입니다"}.get(e.status_code, str(e.detail))
        return error_response(e.status_code, code, msg)

    @app.exception_handler(psycopg.errors.ExclusionViolation)
    async def _exclusion(_: Request, e: psycopg.Error):
        # store_time_rules 의 EXCLUDE(같은 점포·요일에서 시간대 겹침) 만 있다 (db/001)
        return error_response(409, "TIME_RULE_OVERLAP", "같은 요일에 겹치는 시간대 규칙이 이미 있습니다")

    @app.exception_handler(psycopg.errors.CheckViolation)
    async def _check(_: Request, e: psycopg.Error):
        return error_response(422, "CHECK_VIOLATION", f"입력값이 DB 제약을 어겼습니다 ({_constraint(e)})")

    @app.exception_handler(psycopg.errors.UniqueViolation)
    async def _unique(_: Request, e: psycopg.Error):
        code, msg = UNIQUE_CODES.get(_constraint(e), ("CONFLICT", f"이미 있는 값입니다 ({_constraint(e)})"))
        return error_response(409, code, msg)

    @app.exception_handler(psycopg.errors.NotNullViolation)
    async def _not_null(_: Request, e: psycopg.Error):
        return error_response(422, "CHECK_VIOLATION", f"필수 값이 비어 있습니다 ({e.diag.column_name or ''})")

    @app.exception_handler(psycopg.errors.ForeignKeyViolation)
    async def _fk(_: Request, e: psycopg.Error):
        return error_response(409, "IN_USE", f"다른 데이터가 참조하고 있어 처리할 수 없습니다 ({_constraint(e)})")

    @app.exception_handler(psycopg.errors.InvalidDatetimeFormat)
    async def _bad_dt(_: Request, e: psycopg.Error):
        return error_response(422, "VALIDATION_ERROR", "날짜 · 시각 형식이 올바르지 않습니다")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception):
        # 원인은 로그에만. 응답에는 내부 정보를 싣지 않는다
        log.exception("unhandled error: %r", e)
        return error_response(500, "INTERNAL_ERROR", "서버 내부 오류가 발생했습니다")
