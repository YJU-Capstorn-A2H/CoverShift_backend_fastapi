# app/routers/auth.py
# 管理者の登録・ログイン・ログアウト / 관리자 등록・로그인・로그아웃（仕様書 V9 2-2）
# 根拠 / 근거: V9 2-2「認証API」、基準文書 11/04（招待コードで交代）・11/08（コードは運営が発行）
# 下書き: 提案の部分は「（提案）」と書いてある。 / 초안: 제안인 부분은 「(제안)」이라고 적혀 있다.
import psycopg
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator

from app.core.deps import current_manager
from app.core.errors import ApiError
from app.core.security import create_access_token, hash_password, sha256_hex, verify_password
from app.core.tx import tx

router = APIRouter(prefix="/auth")

_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterIn(BaseModel):
    invite_code: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=254, pattern=_EMAIL_RE)
    password: str = Field(min_length=8)                      # 8文字以上は（提案）/ 8자 이상은 (제안)

    @field_validator("password")
    @classmethod
    def _bcrypt_limit(cls, v: str) -> str:
        # bcrypt は72バイトまで。超えると切り捨てられるので先に断る（security.py のコメントどおり）
        # bcrypt는 72바이트까지. 넘으면 잘리므로 먼저 거절한다(security.py의 주석대로)
        if len(v.encode("utf-8")) > 72:
            raise ValueError("パスワードは72バイトまで / 비밀번호는 72바이트까지")
        return v


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=200)




def _invalid_code() -> ApiError:
    # 存在しない・期限切れ・使用済みを区別しない（V9: 同じ400）/ 존재하지 않음・만료・사용 완료를 구분하지 않는다(V9: 같은 400)
    return ApiError(400, "INVALID_CODE", "招待コードが無効です / 초대 코드가 유효하지 않습니다")


# 仕様書V9 2-2 認証API｜管理者の登録（招待コード） / 명세서 V9 2-2 인증 API｜관리자 등록(초대 코드)
@router.post("/register", status_code=201, summary="管理者の登録（招待コード）/ 관리자 등록(초대 코드)")
def register(body: RegisterIn):
    """1トランザクション: コード確認 → 新アカウント作成 → 店の管理者を付け替え → 旧アカウント無効 → コード使用済み。
    1트랜잭션: 코드 확인 → 새 계정 생성 → 점포 관리자 교체 → 옛 계정 무효 → 코드 사용 완료."""
    email = body.email.strip().lower()
    code_hash = sha256_hex(body.invite_code)
    pw_hash = hash_password(body.password)
    try:
        with tx() as conn:
            inv = conn.execute(
                "SELECT store_id FROM invite_codes "
                "WHERE code = %s AND used_at IS NULL AND expires_at > now() FOR UPDATE", (code_hash,)).fetchone()
            if inv is None:
                raise _invalid_code()
            if conn.execute("SELECT 1 FROM manager_accounts WHERE email = %s", (email,)).fetchone():
                raise ApiError(409, "EMAIL_EXISTS", "このメールは登録済みです / 이 이메일은 이미 등록되어 있습니다")

            new_id = conn.execute(
                "INSERT INTO manager_accounts (email, password_hash) VALUES (%s, %s) RETURNING manager_id",
                (email, pw_hash)).fetchone()["manager_id"]
            old = conn.execute("SELECT manager_id FROM stores WHERE store_id = %s FOR UPDATE",
                               (inv["store_id"],)).fetchone()
            conn.execute("UPDATE stores SET manager_id = %s WHERE store_id = %s", (new_id, inv["store_id"]))
            conn.execute("UPDATE manager_accounts SET is_active = false WHERE manager_id = %s",
                         (old["manager_id"],))                      # 消さない（基準文書）/ 지우지 않는다(기준 문서)
            conn.execute("UPDATE invite_codes SET used_at = now(), used_by = %s WHERE code = %s",
                         (new_id, code_hash))
    except psycopg.errors.UniqueViolation:
        # 同時に同じメールで登録された場合の最後の砦 / 동시에 같은 이메일로 등록된 경우의 마지막 방어선
        raise ApiError(409, "EMAIL_EXISTS", "このメールは登録済みです / 이 이메일은 이미 등록되어 있습니다")
    return {"manager_id": new_id}


# メールが無いときもパスワード確認を1回走らせ、応答時間でメールの有無が分からないようにする（提案）
# 이메일이 없을 때도 비밀번호 확인을 1번 돌려, 응답 시간으로 이메일 유무를 알 수 없게 한다(제안)
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password-for-timing")
    return _DUMMY_HASH


# 仕様書V9 2-2 認証API｜管理者ログイン / 명세서 V9 2-2 인증 API｜관리자 로그인
@router.post("/manager/login", summary="管理者ログイン / 관리자 로그인")
def login(body: LoginIn):
    with tx() as conn:
        row = conn.execute("SELECT manager_id, password_hash, is_active FROM manager_accounts WHERE email = %s",
                           (body.email.strip().lower(),)).fetchone()
    ok = verify_password(body.password, row["password_hash"] if row else _dummy_hash())
    if row is None or not ok or not row["is_active"]:
        # 理由を分けない（V9: 同じ401）/ 이유를 구분하지 않는다(V9: 같은 401)
        raise ApiError(401, "UNAUTHORIZED", "メールまたはパスワードが違います / 이메일 또는 비밀번호가 다릅니다")
    return {"access_token": create_access_token(row["manager_id"]), "manager_id": row["manager_id"]}


# 仕様書V9 2-2 認証API｜ログアウト / 명세서 V9 2-2 인증 API｜로그아웃
@router.post("/logout", status_code=204, summary="ログアウト / 로그아웃")
def logout(me: dict = Depends(current_manager)):
    # サーバー側でトークンを失効させる仕組みは無い（V9「未実装」）。クライアントがトークンを捨てる。
    # 서버 쪽에서 토큰을 폐기하는 장치는 없다(V9 「미구현」). 클라이언트가 토큰을 버린다.
    return Response(status_code=204)
