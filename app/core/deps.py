# app/core/deps.py
# 管理者JWTの確認。店舗はJWTの管理者から決める → リクエストに store_id は載せない
# 관리자 JWT 확인. 점포는 JWT의 관리자에서 정한다 → 요청에 store_id를 싣지 않는다
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.tx import tx
from app.core.errors import ApiError
from app.core.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)

_SQL_MANAGER = """
SELECT m.manager_id, m.email, s.store_id
FROM manager_accounts m JOIN stores s ON s.manager_id = m.manager_id
WHERE m.is_active {extra}
"""


def current_manager(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    """ログイン中の管理者 {manager_id, email, store_id} を返す。
    로그인 중인 관리자 {manager_id, email, store_id}를 돌려준다."""
    if cred is None:
        if settings.dev_skip_auth:
            # 開発用: 最初の有効な管理者として動く / 개발용: 첫 번째 유효 관리자로 동작
            with tx() as conn:
                row = conn.execute(_SQL_MANAGER.format(extra="ORDER BY m.manager_id LIMIT 1")).fetchone()
            if row:
                return row
        raise ApiError(401, "UNAUTHORIZED", "ログインが必要です / 로그인이 필요합니다")

    manager_id = decode_access_token(cred.credentials)
    if manager_id is None:
        raise ApiError(401, "UNAUTHORIZED", "トークンが無効です / 토큰이 유효하지 않습니다")

    # 毎回 is_active を確認する → 交代で無効になった旧アカウントのJWTは使えなくなる
    # 매번 is_active를 확인한다 → 교대로 무효가 된 옛 계정의 JWT는 쓸 수 없게 된다
    with tx() as conn:
        row = conn.execute(_SQL_MANAGER.format(extra="AND m.manager_id = %s"), (manager_id,)).fetchone()
    if row is None:
        raise ApiError(401, "UNAUTHORIZED", "トークンが無効です / 토큰이 유효하지 않습니다")
    return row