# app/routers/auth.py
# 000-認証 / 000-인증（仕様書 2-2・6章）
import re

from fastapi import APIRouter, Depends, Response
from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.core.db import tx
from app.core.deps import current_manager
from app.core.errors import ApiError
from app.core.security import create_access_token, hash_password, sha256_hex, verify_password

router = APIRouter()

# 簡易なメール形式（外部ライブラリなし）/ 간이 이메일 형식(외부 라이브러리 없음)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_email(v: str) -> str:
    v = v.strip().lower()
    if not _EMAIL.match(v) or len(v) > 254:
        raise ValueError("メール形式が正しくありません / 이메일 형식이 올바르지 않습니다")
    return v


class RegisterIn(BaseModel):
    # 6章は invite_code、2章は code と書いてある → どちらも受け付ける
    # 6장은 invite_code, 2장은 code로 적혀 있다 → 둘 다 받는다
    invite_code: str = Field(validation_alias=AliasChoices("invite_code", "code"), min_length=1, max_length=200)
    email: str
    password: str = Field(min_length=8)

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return _check_email(v)

    @field_validator("password")
    @classmethod
    def _pw_len(cls, v):
        if len(v.encode("utf-8")) > 72:    # bcrypt の上限 / bcrypt 상한
            raise ValueError("パスワードは72バイトまで / 비밀번호는 72바이트까지")
        return v


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/auth/register", status_code=201, summary="管理者の登録（招待コード） / 관리자 등록(초대 코드)")
def register(body: RegisterIn):
    """1トランザクションで: コード確認 → 新アカウント作成 → 店の管理者を差し替え → 旧アカウント無効 → コードを使用済みに。
    コードの不備は理由を区別せず同じ 400（存在しない・期限切れ・使用済み）。
    1트랜잭션으로: 코드 확인 → 새 계정 생성 → 점포 관리자 교체 → 옛 계정 무효 → 코드를 사용 완료로.
    코드 문제는 이유를 구분하지 않고 같은 400(없음・만료・사용 완료)."""
    invalid = ApiError(400, "INVALID_CODE", "招待コードが正しくありません / 초대 코드가 올바르지 않습니다")
    with tx() as conn:
        code = conn.execute(
            "SELECT store_id, used_at, (expires_at <= now()) AS expired "
            "FROM invite_codes WHERE code = %s FOR UPDATE",       # 同時に2人が使うのを防ぐ / 동시에 2명이 쓰는 것을 막는다
            (sha256_hex(body.invite_code),),
        ).fetchone()
        if code is None or code["used_at"] is not None or code["expired"]:
            raise invalid

        # コードの確認が先（コードを持たない人にメールの有無を教えない）
        # 코드 확인이 먼저(코드가 없는 사람에게 이메일 존재 여부를 알려주지 않는다)
        if conn.execute("SELECT 1 FROM manager_accounts WHERE email = %s", (body.email,)).fetchone():
            raise ApiError(409, "EMAIL_EXISTS", "このメールは登録済みです / 이 이메일은 이미 등록되어 있습니다")

        new_id = conn.execute(
            "INSERT INTO manager_accounts (email, password_hash) VALUES (%s, %s) RETURNING manager_id",
            (body.email, hash_password(body.password)),
        ).fetchone()["manager_id"]

        store = conn.execute("SELECT manager_id FROM stores WHERE store_id = %s FOR UPDATE", (code["store_id"],)).fetchone()
        conn.execute("UPDATE stores SET manager_id = %s WHERE store_id = %s", (new_id, code["store_id"]))
        # 旧アカウントは消さず無効にする（承認の履歴が残る）/ 옛 계정은 지우지 않고 무효로 한다(승인 이력이 남는다)
        conn.execute("UPDATE manager_accounts SET is_active = false WHERE manager_id = %s", (store["manager_id"],))
        conn.execute("UPDATE invite_codes SET used_at = now(), used_by = %s WHERE code = %s",
                     (new_id, sha256_hex(body.invite_code)))
    return {"manager_id": new_id}


@router.post("/auth/manager/login", summary="管理者ログイン / 관리자 로그인")
def manager_login(body: LoginIn):
    """メール・パスワードが違っても、無効なアカウントでも、同じ 401。
    이메일・비밀번호가 틀려도, 무효 계정이어도, 같은 401."""
    fail = ApiError(401, "INVALID_CREDENTIALS", "メールまたはパスワードが正しくありません / 이메일 또는 비밀번호가 올바르지 않습니다")
    with tx() as conn:
        row = conn.execute(
            "SELECT manager_id, password_hash, is_active FROM manager_accounts WHERE email = %s",
            (body.email.strip().lower(),),
        ).fetchone()
    if row is None or not row["is_active"] or not verify_password(body.password, row["password_hash"]):
        raise fail
    return {"access_token": create_access_token(row["manager_id"]), "token_type": "bearer",
            "manager_id": row["manager_id"]}


@router.post("/auth/logout", status_code=204, summary="ログアウト / 로그아웃")
def logout(_: dict = Depends(current_manager)):
    # サーバー側のトークン失効は未実装（仕様どおり）。クライアントがトークンを捨てる。
    # 서버 측 토큰 폐기는 미구현(사양대로). 클라이언트가 토큰을 버린다.
    return Response(status_code=204)
