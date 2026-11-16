# app/core/errors.py
# 共通のエラー形式: {"error": {"code": "...", "message": "..."}}（仕様書 6章の共通ルール）
# 공통 에러 형식: {"error": {"code": "...", "message": "..."}}(사양서 6장의 공통 규칙)
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import Error as PgError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    """想定内のエラー。status・code・message をそのまま返す。
    예상 범위 안의 에러. status・code・message를 그대로 돌려준다."""

    def __init__(self, status: int, code: str, message: str, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _body(code: str, message: str, details=None) -> dict:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = jsonable_encoder(details)     # 日付などをJSONにできる形へ / 날짜 등을 JSON으로 만들 수 있는 형태로
    return {"error": err}


_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 410: "GONE", 422: "VALIDATION_ERROR"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "ERROR")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # 入力の形が違う / 입력 형식이 다르다
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body("VALIDATION_ERROR", "入力が正しくありません / 입력이 올바르지 않습니다", details),
                            status_code=422)

    @app.exception_handler(PgError)
    async def _db_error(_: Request, exc: PgError):
        # DBの制約違反を HTTP に変換する。SQLの中身は外に出さない。
        # DB 제약 위반을 HTTP로 변환한다. SQL 내용은 밖으로 내보내지 않는다.
        state = getattr(exc, "sqlstate", None) or ""
        if state in ("23505", "23P01"):      # 一意制約・EXCLUDE（時間帯の重なり）/ 유일 제약・EXCLUDE(시간대 겹침)
            return JSONResponse(_body("CONFLICT", "既に存在するか、範囲が重なっています / 이미 존재하거나 범위가 겹칩니다"), status_code=409)
        if state == "23503":                 # 参照先が無い / 참조 대상이 없다
            return JSONResponse(_body("REFERENCE_ERROR", "参照先が存在しません / 참조 대상이 없습니다"), status_code=409)
        if state in ("23514", "23502") or state.startswith("22"):   # CHECK・NOT NULL・値の形式 / CHECK・NOT NULL・값 형식
            return JSONResponse(_body("VALIDATION_ERROR", "値がルールに合っていません / 값이 규칙에 맞지 않습니다"), status_code=422)
        return JSONResponse(_body("DB_ERROR", "DBエラー / DB 오류"), status_code=500)
