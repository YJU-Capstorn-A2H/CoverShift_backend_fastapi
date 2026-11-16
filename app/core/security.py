# app/core/security.py
# パスワードのハッシュ（bcrypt）、JWT、コードのハッシュ
# 비밀번호 해시(bcrypt), JWT, 코드 해시
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def _secret() -> str:
    # 未設定なら起動時ではなく使う時に失敗させる / 미설정이면 기동 시가 아니라 사용 시점에 실패시킨다
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET が未設定です / JWT_SECRET이 설정되지 않았습니다")
    return settings.jwt_secret


ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    # bcrypt は72バイトまで。呼び出し側で長さを確認する
    # bcrypt는 72바이트까지. 호출 측에서 길이를 확인한다
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def sha256_hex(value: str) -> str:
    # 招待コードはDBに「ハッシュ」だけ保存する（仕様書 6章 register）
    # 초대 코드는 DB에 「해시」만 저장한다(사양서 6장 register)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_access_token(manager_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(manager_id), "typ": "manager", "iat": now,
               "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> int | None:
    # 不正・期限切れは None / 부정・만료는 None
    try:
        data = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    if data.get("typ") != "manager":
        return None
    try:
        return int(data["sub"])
    except (KeyError, ValueError):
        return None