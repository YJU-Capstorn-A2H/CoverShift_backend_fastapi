"""관리자 계정 · 점포 · 초대 코드 (M-4, A-1 ~ A-4). SQL 은 여기만. 트랜잭션 경계는 부르는 쪽(요청 1개 / 운영 스크립트 1번).

- 비밀번호: bcrypt (V9_ko 982 「비밀번호는 bcrypt 해시」).
- 초대 코드: invite_codes.code 에 sha256(code) 16진수를 넣는다 — 원문은 운영 스크립트가 한 번만 출력한다.
  (가정 신규) V9_ko 433 「초대 코드는 해시 저장 권장」 + 열이 TEXT 라 해시 문자열을 그대로 PK 로 쓴다.
  bcrypt 가 아니라 sha256 인 이유: PK 로 바로 찾아야 해서(솔트가 있으면 찾을 수 없다). 코드는 무작위 96비트라 사전 공격 대상이 아니다.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

import bcrypt
import psycopg

BCRYPT_ROUNDS = 12  # 시험은 이 값을 낮춰 빨리 돈다 (tests/api/conftest.py)
INVITE_TTL_SQL = "interval '1 day'"  # V9_ko 155 「1회용 · 유효 1일」 (A-4)

_dummy_hash: bytes | None = None


def normalize_email(email: str) -> str:
    # (가정 신규) 대소문자 · 앞뒤 공백이 달라도 같은 계정으로 본다. email 열 UNIQUE 는 대소문자를 구분하므로 저장 전에 맞춘다
    return email.strip().lower()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:  # 저장된 해시가 bcrypt 모양이 아님
        return False


def _burn_time(password: str) -> None:
    """없는 이메일도 bcrypt 1번만큼 시간을 쓴다 → 응답 시간으로 이메일 유무를 알 수 없게 (V9_ko 151 「같은 401」)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt(BCRYPT_ROUNDS))
    try:
        bcrypt.checkpw(password.encode(), _dummy_hash)
    except ValueError:  # 72바이트 넘는 비밀번호 — bcrypt 5 는 거부한다. 결과는 어차피 실패
        pass


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


# ---------------------------------------------------------------- 운영 스크립트용 (A-4)

def create_store(conn: psycopg.Connection, *, store_name: str, email: str, password: str) -> dict:
    """최초 점포 + 첫 관리자 (V9_ko 44 「A. 최초 1명: 운영 스크립트」, V9_ko 431)."""
    m = conn.execute(
        "INSERT INTO manager_accounts (email, password_hash) VALUES (%s, %s) RETURNING manager_id",
        (normalize_email(email), hash_password(password))).fetchone()
    s = conn.execute("INSERT INTO stores (manager_id, name) VALUES (%s, %s) RETURNING store_id",
                     (m["manager_id"], store_name)).fetchone()
    return {"store_id": s["store_id"], "manager_id": m["manager_id"]}


def issue_invite(conn: psycopg.Connection, store_id: int) -> tuple[str, datetime]:
    """1회용 · 1일 초대 코드. 원문 코드는 돌려주기만 하고 DB 에는 해시만 (V9_ko 46, 155, 433)."""
    if conn.execute("SELECT 1 FROM stores WHERE store_id = %s", (store_id,)).fetchone() is None:
        raise LookupError(f"store {store_id} not found")
    code = secrets.token_urlsafe(12)  # 16글자 · 96비트
    row = conn.execute(
        f"INSERT INTO invite_codes (code, store_id, expires_at) VALUES (%s, %s, now() + {INVITE_TTL_SQL}) "
        "RETURNING expires_at", (hash_code(code), store_id)).fetchone()
    return code, row["expires_at"]


# ---------------------------------------------------------------- API 용

def register(conn: psycopg.Connection, *, invite_code: str, email: str, password: str) -> int | None:
    """A-1. 한 트랜잭션(부르는 쪽): 코드 검증 → 계정 생성 → stores.manager_id 교체 → 구 계정 is_active=false → used_at/used_by.

    순서는 V9_ko 149 그대로. 코드가 없음 · 만료 · 사용됨이면 None (사유를 나누지 않는다, V9_ko 57 · 149).
    이메일 중복은 INSERT 의 UniqueViolation 그대로 올린다 → 처리기가 409 EMAIL_EXISTS, 트랜잭션이 통째로 취소돼 코드도 안 쓰인다.
    FOR UPDATE: 같은 코드로 동시에 두 명이 등록하면 뒤 사람은 앞 사람 commit 뒤 조건(used_at IS NULL)을 다시 보고 None.
    """
    inv = conn.execute(
        """SELECT code, store_id FROM invite_codes
           WHERE code = %s AND used_at IS NULL AND expires_at > now() FOR UPDATE""",
        (hash_code(invite_code),)).fetchone()
    if inv is None:
        return None
    old = conn.execute("SELECT manager_id FROM stores WHERE store_id = %s FOR UPDATE", (inv["store_id"],)).fetchone()
    new_id = conn.execute(
        "INSERT INTO manager_accounts (email, password_hash) VALUES (%s, %s) RETURNING manager_id",
        (normalize_email(email), hash_password(password))).fetchone()["manager_id"]
    conn.execute("UPDATE stores SET manager_id = %s WHERE store_id = %s", (new_id, inv["store_id"]))
    conn.execute("UPDATE manager_accounts SET is_active = false WHERE manager_id = %s", (old["manager_id"],))
    conn.execute("UPDATE invite_codes SET used_at = now(), used_by = %s WHERE code = %s", (new_id, inv["code"]))
    return new_id


def authenticate(conn: psycopg.Connection, email: str, password: str) -> dict | None:
    """A-2. 활성 + 점포가 있는 계정만. 실패 사유(이메일 없음 · 비밀번호 틀림 · 무효 계정)는 나누지 않는다 (V9_ko 151)."""
    row = conn.execute(
        """SELECT m.manager_id, m.password_hash, s.store_id
           FROM manager_accounts m JOIN stores s ON s.manager_id = m.manager_id
           WHERE m.email = %s AND m.is_active""", (normalize_email(email),)).fetchone()
    if row is None:
        _burn_time(password)
        return None
    if not check_password(password, row["password_hash"]):
        return None
    return {"manager_id": row["manager_id"], "store_id": row["store_id"]}


def get_active_manager(conn: psycopg.Connection, manager_id: int) -> dict | None:
    """A-3 (G15). 요청마다 부른다. 무효 계정 · 점포 없는 계정은 None."""
    return conn.execute(
        """SELECT m.manager_id, m.email, s.store_id, s.name AS store_name
           FROM manager_accounts m JOIN stores s ON s.manager_id = m.manager_id
           WHERE m.manager_id = %s AND m.is_active""", (manager_id,)).fetchone()
