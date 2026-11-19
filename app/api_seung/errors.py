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

BUSY_MESSAGE = "いま編成を処理しています"  # V9_ko 509 (A-15). 화면이 이 문장을 그대로 보여 준다


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def not_found(what: str = "リソース") -> ApiError:
    # 남의 점포 자원도 같은 404 (A-5, API_CONTRACT 「남의 점포 자원 = 404」) — 있는지 없는지 알려 주지 않는다
    return ApiError(404, "NOT_FOUND", f"{what}が見つかりません")


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


# UNIQUE 제약 이름 → (코드, 문장). 이름은 PostgreSQL 기본 규칙(<표>_<열>_key)으로 db/001 에서 정해진다.
UNIQUE_CODES: dict[str, tuple[str, str]] = {
    "manager_accounts_email_key": ("EMAIL_EXISTS", "すでに登録されているメールアドレスです"),
    "shift_periods_store_id_target_month_key": ("PERIOD_EXISTS", "その月のシフト期間はすでにあります"),
    "employees_line_user_id_key": ("LINE_ALREADY_LINKED", "このLINEアカウントは、すでに別の従業員に連携されています"),
    # (가정 신규) 계약에 코드가 없다. UNIQUE(store_id, name) (V9_ko tasks DDL) 를 409 로 돌리는 이름만 정했다
    "tasks_store_id_name_key": ("TASK_EXISTS", "同じ名前の業務がすでにあります"),
    "one_approval_per_draft": ("ALREADY_APPROVED", "この案はすでに承認されています"),  # A-16 (V9_ko 505)
    "one_confirmed_draft_per_period": ("ALREADY_CONFIRMED", "この期間には、確定済みの案がすでにあります"),  # A-16
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
        msg = f"入力値が正しくありません: {loc} {first.get('msg', '')}".strip()
        return error_response(422, "VALIDATION_ERROR", msg)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(
            e.status_code, f"HTTP_{e.status_code}")
        msg = {404: "見つかりません", 405: "許可されていないメソッドです"}.get(e.status_code, str(e.detail))
        return error_response(e.status_code, code, msg)

    @app.exception_handler(psycopg.errors.ExclusionViolation)
    async def _exclusion(_: Request, e: psycopg.Error):
        # store_time_rules 의 EXCLUDE(같은 점포·요일에서 시간대 겹침) 만 있다 (db/001)
        return error_response(409, "TIME_RULE_OVERLAP", "同じ曜日に、重なる時間帯ルールがすでにあります")

    @app.exception_handler(psycopg.errors.CheckViolation)
    async def _check(_: Request, e: psycopg.Error):
        return error_response(422, "CHECK_VIOLATION", f"入力値がDBの制約に違反しています ({_constraint(e)})")

    @app.exception_handler(psycopg.errors.UniqueViolation)
    async def _unique(_: Request, e: psycopg.Error):
        code, msg = UNIQUE_CODES.get(_constraint(e), ("CONFLICT", f"すでにある値です ({_constraint(e)})"))
        return error_response(409, code, msg)

    @app.exception_handler(psycopg.errors.NotNullViolation)
    async def _not_null(_: Request, e: psycopg.Error):
        return error_response(422, "CHECK_VIOLATION", f"必須の値が空です ({e.diag.column_name or ''})")

    @app.exception_handler(psycopg.errors.ForeignKeyViolation)
    async def _fk(_: Request, e: psycopg.Error):
        return error_response(409, "IN_USE", f"ほかのデータが参照しているため、処理できません ({_constraint(e)})")

    @app.exception_handler(psycopg.errors.InvalidDatetimeFormat)
    async def _bad_dt(_: Request, e: psycopg.Error):
        return error_response(422, "VALIDATION_ERROR", "日付・時刻の形式が正しくありません")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception):
        # 원인은 로그에만. 응답에는 내부 정보를 싣지 않는다
        log.exception("unhandled error: %r", e)
        return error_response(500, "INTERNAL_ERROR", "サーバー内部でエラーが起きました")
