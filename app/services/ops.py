# app/services/ops.py
# 運営用の処理（APIではなくスクリプトから呼ぶ）。基準文書 11/04・11/08:
#  - 最初の店舗・最初の管理者は運営がスクリプトで作る
#  - 招待コード（引き継ぎ用）も運営が発行する（1回用・有効1日）
# 운영용 처리(API가 아니라 스크립트에서 호출). 기준 문서 11/04・11/08:
#  - 첫 점포・첫 관리자는 운영 측이 스크립트로 만든다
#  - 초대 코드(인수인계용)도 운영 측이 발급한다(1회용・유효 1일)
import secrets

from app.core.security import hash_password, sha256_hex


def create_store_with_manager(conn, store_name: str, email: str, password: str) -> dict:
    """最初の管理者と店舗を同時に作る。stores.manager_id が NOT NULL なので、管理者が先。
    첫 관리자와 점포를 동시에 만든다. stores.manager_id가 NOT NULL이므로 관리자가 먼저."""
    m = conn.execute(
        "INSERT INTO manager_accounts (email, password_hash) VALUES (%s, %s) RETURNING manager_id",
        (email.strip().lower(), hash_password(password)),
    ).fetchone()
    s = conn.execute(
        "INSERT INTO stores (manager_id, name) VALUES (%s, %s) RETURNING store_id",
        (m["manager_id"], store_name),
    ).fetchone()
    return {"manager_id": m["manager_id"], "store_id": s["store_id"]}


def create_invite_code(conn, store_id: int) -> dict:
    """招待コードを発行する。平文は戻り値でだけ返す（DBにはハッシュのみ）。有効1日。
    초대 코드를 발급한다. 평문은 반환값으로만 돌려준다(DB에는 해시만). 유효 1일."""
    code = secrets.token_urlsafe(16)
    row = conn.execute(
        "INSERT INTO invite_codes (code, store_id, expires_at) VALUES (%s, %s, now() + interval '1 day') "
        "RETURNING expires_at",
        (sha256_hex(code), store_id),
    ).fetchone()
    return {"code": code, "expires_at": row["expires_at"]}
