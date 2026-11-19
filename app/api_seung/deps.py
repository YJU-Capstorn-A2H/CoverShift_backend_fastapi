"""라우트가 같이 쓰는 의존성: DB 연결(요청 1개 = 트랜잭션 1개), 관리자 인증, 러너 · LLM, 입구 잠금.

러너(app/runner.py)는 runtime 에이전트, LLM(app/llm)은 llm 에이전트 소유. 여기서는 계약 이름만 쓴다
(API_CONTRACT 「러너 계약」: Busy · NotWaiting · get_runner / app.llm.get_composer).
시험에서는 app.dependency_overrides[get_runner] · [get_composer] 로 가짜를 넣는다.
"""
from __future__ import annotations

import zlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
import psycopg
from fastapi import Depends, Header, Request

from app import runner as _runner_mod
from app.config import get_settings
from app.db import connect
from app.llm import get_composer  # 시험 · 통합이 dependency_overrides[get_composer] 로 바꿔 끼운다
from app.repo import managers
from app.runner import Busy, NotWaiting, get_runner  # 러너 계약 이름 (API_CONTRACT 「러너 계약」)

from .errors import BUSY_MESSAGE, ApiError

# 계약 밖 예외 — runtime 에이전트가 (가정 신규)로 더한 것 (app/runner.py). 계약에 없으므로 이름이 없어져도 API 가 깨지지 않게
# getattr 로만 읽고, 없으면 절대 던져지지 않는 형으로 대신한다. 「계약 변경 요청」에 올림.
AlreadyStarted = getattr(_runner_mod, "AlreadyStarted", type("AlreadyStarted", (Exception,), {}))
StaleDraft = getattr(_runner_mod, "StaleDraft", type("StaleDraft", (NotWaiting,), {}))


# ---------------------------------------------------------------- DB

def get_conn():
    """요청 1개 = 트랜잭션 1개. 정상 종료 → commit, 예외 → rollback (psycopg Connection 의 with 규칙).

    scope="function" 으로 등록한다: commit 이 응답을 보내기 **전에** 끝나야 화면이 바로 다음 요청에서 그 값을 본다.
    """
    with connect() as c:
        yield c


Conn = Annotated[psycopg.Connection, Depends(get_conn, scope="function")]
ComposerDep = Annotated[Any, Depends(get_composer)]


# ---------------------------------------------------------------- 러너 (늦게 만든다)

class LazyRunner:
    """러너 계약 3개(start · status · resume)를 처음 부를 때 get_runner() 를 부른다.

    이유: RUNNER=local 이면 get_runner() 가 그래프 · 체크포인트 연결을 만든다. 기간 목록처럼 러너가 필요 없을 수도 있는
    요청(thread 없음 · 확정됨)까지 그 준비에 묶이면, 러너 쪽 문제로 설정 화면이 같이 죽는다.
    """

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._runner: Any = None

    def _get(self) -> Any:
        if self._runner is None:
            self._runner = self._factory()
        return self._runner

    def start(self, period_id: int, thread_id: str) -> dict:
        return self._get().start(period_id, thread_id)

    def status(self, period_id: int, thread_id: str | None) -> dict:
        return self._get().status(period_id, thread_id)

    def resume(self, period_id: int, thread_id: str, payload: dict) -> dict:
        return self._get().resume(period_id, thread_id, payload)


def lazy_runner(request: Request) -> LazyRunner:
    # app.dependency_overrides[get_runner] 를 그대로 따른다 (시험 · 통합 시험이 get_runner 이름으로 바꿔 끼운다)
    return LazyRunner(request.app.dependency_overrides.get(get_runner, get_runner))


Runner = Annotated[LazyRunner, Depends(lazy_runner)]


# ---------------------------------------------------------------- 관리자 인증 (A-2, A-3, A-14, G15)

@dataclass(frozen=True)
class CurrentManager:
    manager_id: int
    store_id: int
    email: str
    store_name: str


def _unauthorized(msg: str = "ログインが必要です") -> ApiError:
    return ApiError(401, "UNAUTHORIZED", msg)


def jwt_secret() -> str:
    """빈 비밀 키로는 서명도 검증도 하지 않는다 (.env.example 의 JWT_SECRET= 를 그대로 복사한 경우 위조 가능해진다).

    (가정 신규) 원래는 설정(app/config.py)이 시작할 때 막아야 한다 → 「계약 변경 요청」에 올림.
    """
    secret = get_settings().jwt_secret
    if not secret.strip():
        raise ApiError(500, "SERVER_MISCONFIGURED", "サーバー設定(JWT_SECRET)が空です")
    return secret


def current_manager(conn: Conn, authorization: Annotated[str | None, Header()] = None) -> CurrentManager:
    """JWT 확인 + 요청마다 manager_accounts.is_active 확인 (V9_ko 56 → G15).

    - 점포는 JWT 의 store_id 가 아니라 DB(stores.manager_id = 이 관리자)에서 다시 읽는다 (A-5, V9_ko 167).
      교대 뒤 구 관리자는 is_active=false 이고 stores 에도 없으므로 둘 다에서 걸린다.
    - DEV_SKIP_AUTH=true 이고 헤더가 **없을 때만** manager_id=1 (V9_ko 511, A-14). 헤더가 있으면 끝까지 검사한다.
    """
    claim_store: int | None = None
    if authorization is None:
        if not get_settings().dev_skip_auth:
            raise _unauthorized()
        manager_id = 1
    else:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized("Authorization ヘッダーは Bearer トークンにしてください")
        try:
            claims = jwt.decode(token.strip(), jwt_secret(), algorithms=["HS256"],
                                options={"require": ["exp", "sub"]})
            manager_id = int(claims["sub"])
            claim_store = int(claims["store_id"]) if claims.get("store_id") is not None else None
        except (jwt.PyJWTError, ValueError, TypeError):
            raise _unauthorized("ログインの有効期限が切れているか、正しくありません")
    row = managers.get_active_manager(conn, manager_id)
    if row is None or (claim_store is not None and claim_store != row["store_id"]):
        raise _unauthorized("このアカウントは使えません。もう一度ログインしてください")
    return CurrentManager(row["manager_id"], row["store_id"], row["email"], row["store_name"])


Manager = Annotated[CurrentManager, Depends(current_manager)]


# ---------------------------------------------------------------- 입구 잠금 (V9_ko 505, 509)

# 두 정수 키 형식의 advisory lock 은 bigint 키 형식과 키 공간이 겹치지 않는다(PostgreSQL 문서 「Advisory Locks」).
# 러너가 자기 잠금을 bigint 키로 잡아도 이 잠금과 부딪치지 않게 첫 키를 이 상수로 고정한다. (가정 신규) 값은 임의
ENTRANCE_LOCK_NS = 0x43535631  # "CSV1"


def _lock_key(thread_id: str) -> int:
    return zlib.crc32(thread_id.encode()) - 2**31  # int4 범위로


@contextmanager
def entrance_lock(conn: psycopg.Connection, thread_id: str):
    """같은 thread 에 대한 지금 시작 · 승인 · 답장 요청이 겹치면 하나만 통과 (V9_ko 505 「입구 잠금」, 509, A-15).

    세션 잠금(pg_try_advisory_lock)이라 중간에 commit 해도 풀리지 않는다 → 「DB 에 thread_id 저장 → 러너 호출」 전체를 덮는다.
    러너 쪽 Busy 와 이중으로 막는다 — 「상태 확인 → resume」 사이의 틈은 러너가 볼 수 없어서 여기서 닫는다.
    행 잠금(FOR UPDATE)을 쓰지 않는 이유: 러너가 돌리는 그래프가 같은 행을 고치면 서로 기다릴 수 있다.
    """
    key = _lock_key(thread_id)
    ok = conn.execute("SELECT pg_try_advisory_lock(%s, %s) AS ok", (ENTRANCE_LOCK_NS, key)).fetchone()["ok"]
    if not ok:
        raise ApiError(409, "BUSY", BUSY_MESSAGE)
    try:
        yield
    finally:
        try:
            conn.execute("SELECT pg_advisory_unlock(%s, %s)", (ENTRANCE_LOCK_NS, key))
        except psycopg.Error:
            # 트랜잭션이 이미 깨진 경우. 요청이 끝나면 연결을 닫으므로(get_conn) 세션 잠금도 같이 풀린다
            pass
