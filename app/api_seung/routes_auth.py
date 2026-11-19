"""인증 (M-4, A-1 ~ A-3, V9_ko 147-153)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Response

from app.config import get_settings
from app.repo import managers

from .deps import Conn, Manager, jwt_secret
from .errors import ApiError
from .schemas import LoginIn, RegisterIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _invalid_code() -> ApiError:
    # 없음 · 만료 · 사용됨 모두 이 한 가지 (V9_ko 57, 149). 사유를 문장에도 넣지 않는다
    return ApiError(400, "INVALID_CODE", "招待コードが正しくありません")


def _invalid_credentials() -> ApiError:
    return ApiError(401, "INVALID_CREDENTIALS", "メールアドレスまたはパスワードが正しくありません")  # V9_ko 151


def issue_jwt(manager_id: int, store_id: int) -> str:
    """{sub, store_id, exp} HS256 (API_CONTRACT). sub 는 문자열 — PyJWT 2.10 부터 sub 가 문자열이 아니면 decode 가 거부한다."""
    exp = datetime.now(UTC) + timedelta(minutes=get_settings().jwt_ttl_minutes)
    return jwt.encode({"sub": str(manager_id), "store_id": store_id, "exp": exp}, jwt_secret(), algorithm="HS256")


@router.post("/register", status_code=201)
def register(body: RegisterIn, conn: Conn):
    """A-1: 한 트랜잭션 (요청 1개 = 트랜잭션 1개, deps.get_conn). 이메일 중복은 처리기가 409 EMAIL_EXISTS."""
    new_id = managers.register(conn, invite_code=body.invite_code, email=body.email, password=body.password)
    if new_id is None:
        raise _invalid_code()
    return {"manager_id": new_id}


@router.post("/manager/login")
def login(body: LoginIn, conn: Conn):
    """A-2: 이메일 없음 · 비밀번호 틀림 · 무효 계정 모두 같은 401."""
    m = managers.authenticate(conn, body.email, body.password)
    if m is None:
        raise _invalid_credentials()
    return {"access_token": issue_jwt(m["manager_id"], m["store_id"]), "manager_id": m["manager_id"],
            "store_id": m["store_id"]}


@router.post("/logout", status_code=204)
def logout(_: Manager):
    """서버 쪽 토큰 폐기는 하지 않는다 (V9_ko 153). 무효화는 요청마다 is_active 확인(G15) + JWT 기한으로."""
    return Response(status_code=204)


@router.get("/me")
def me(m: Manager):
    return {"manager_id": m.manager_id, "email": m.email, "store_id": m.store_id, "store_name": m.store_name}
