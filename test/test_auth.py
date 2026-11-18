"""E9 認証API（登録・ログイン・ログアウト）のテスト。実際のDBを使う。
E9 인증 API(등록・로그인・로그아웃) 테스트. 실제 DB를 쓴다.

- テストで作るアカウントのメールは全部 @test-auth.example にする。テストの前後で消す。
  테스트에서 만드는 계정의 이메일은 전부 @test-auth.example로 한다. 테스트 전후에 지운다.
- 店舗・管理者・招待コードは ops.py（運営スクリプトと同じ関数）で作る → 本物のハッシュが入る。
  점포・관리자・초대 코드는 ops.py(운영 스크립트와 같은 함수)로 만든다 → 진짜 해시가 들어간다.

基準 / 기준: 仕様書 V9 2-2（同じ401・同じ400・1トランザクション・旧アカウント無効）。
V9 に無く、コード（auth.py）の提案を確かめているテストには【提案】と書いた。決まったら直す。
기준: 명세서 V9 2-2(같은 401・같은 400・한 트랜잭션・옛 계정 무효).
V9에 없고 코드(auth.py)의 제안을 확인하는 테스트에는 【제안】이라고 적었다. 정해지면 고친다.
"""
import datetime as dt
import os
import uuid

import jwt
import psycopg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.core.config import settings
from app.main import app
from app.services.ops import create_invite_code, create_store_with_manager

load_dotenv()

DOMAIN = "@test-auth.example"
PASSWORD = "pw-test-1234"
REGISTER = "/api/auth/register"
LOGIN = "/api/auth/manager/login"
LOGOUT = "/api/auth/logout"


# ---------- 準備 / 준비 ----------

def _email(name: str) -> str:
    # 毎回ちがうメールにする（前のテストの残りとぶつからない）/ 매번 다른 이메일(이전 테스트 잔여물과 안 겹치게)
    return f"{name}-{uuid.uuid4().hex[:8]}{DOMAIN}"


def _cleanup(db):
    # 参照の向きの逆順に消す: 招待コード → 店舗 → 管理者 / 참조 방향의 역순으로 지운다: 초대 코드 → 점포 → 관리자
    mine = f"SELECT manager_id FROM manager_accounts WHERE email LIKE '%{DOMAIN}'"
    stores = f"SELECT store_id FROM stores WHERE manager_id IN ({mine})"
    db.execute(f"DELETE FROM invite_codes WHERE store_id IN ({stores})")
    db.execute(f"DELETE FROM stores WHERE manager_id IN ({mine})")
    db.execute(f"DELETE FROM manager_accounts WHERE email LIKE '%{DOMAIN}'")


@pytest.fixture
def db():
    # autocommit=True: 1行ずつすぐ確定する（テストの準備・確認用）/ 한 줄씩 바로 확정(테스트 준비・확인용)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row) as conn:
        _cleanup(conn)
        yield conn
        _cleanup(conn)


@pytest.fixture
def client(db, monkeypatch):
    # .env の値に左右されないようにする / .env 값에 좌우되지 않게 한다
    monkeypatch.setattr(settings, "dev_skip_auth", False)          # ログイン省略をオフ / 로그인 생략 끄기
    if not settings.jwt_secret:
        monkeypatch.setattr(settings, "jwt_secret", "test-secret-for-pytest-0123456789abcdef")  # .env に無くても動く / .env에 없어도 동작
    with TestClient(app) as c:
        yield c


@pytest.fixture
def store(db):
    """店舗1つと最初の管理者（運営スクリプトと同じ作り方）/ 점포 1개와 첫 관리자(운영 스크립트와 같은 방식)"""
    email = _email("first")
    ids = create_store_with_manager(db, "テスト店 / 테스트 점포", email, PASSWORD)
    return {**ids, "email": email}


def _login(client, email, password=PASSWORD):
    return client.post(LOGIN, json={"email": email, "password": password})


def _token(client, email):
    return _login(client, email).json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- ログイン / 로그인 ----------

def test_login_success(client, store):
    res = _login(client, store["email"])
    assert res.status_code == 200
    body = res.json()
    assert body["manager_id"] == store["manager_id"]
    assert body["access_token"].count(".") == 2          # JWT = 헤더.내용.서명


def test_login_email_is_case_insensitive(client, store):
    # メールは小文字にそろえて保存・検索する / 이메일은 소문자로 맞춰 저장・검색한다
    assert _login(client, store["email"].upper()).status_code == 200


def test_wrong_password_and_unknown_email_get_same_401(client, store):
    wrong = _login(client, store["email"], "wrong-password")
    unknown = _login(client, _email("nobody"))
    assert wrong.status_code == 401 and unknown.status_code == 401
    # 理由を分けない: 本文まで同じ / 이유를 구분하지 않는다: 본문까지 같다
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "UNAUTHORIZED"


def test_inactive_account_cannot_login(client, store, db):
    db.execute("UPDATE manager_accounts SET is_active = false WHERE manager_id = %s", (store["manager_id"],))
    assert _login(client, store["email"]).status_code == 401


def test_login_body_shape_is_checked(client):
    res = client.post(LOGIN, json={"email": "a@b.c"})        # password なし / 없음
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


# ---------- トークン確認（ログアウトで試す）/ 토큰 확인(로그아웃으로 시험) ----------

def test_logout_with_token(client, store):
    assert client.post(LOGOUT, headers=_auth(_token(client, store["email"]))).status_code == 204


def test_no_token_is_401(client):
    assert client.post(LOGOUT).status_code == 401


def test_tampered_token_is_401(client, store):
    token = _token(client, store["email"])
    head, payload, sig = token.split(".")
    bad_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")    # 署名を1文字変える / 서명을 한 글자 바꾼다
    assert client.post(LOGOUT, headers=_auth(f"{head}.{payload}.{bad_sig}")).status_code == 401


def test_token_signed_with_other_key_is_401(client, store):
    # 中身を他人の番号に書き換えても、秘密鍵がなければ正しい署名は作れない
    # 내용을 다른 번호로 바꿔도, 비밀 키가 없으면 올바른 서명을 만들 수 없다
    now = dt.datetime.now(dt.timezone.utc)
    fake = jwt.encode({"sub": str(store["manager_id"]), "typ": "manager", "iat": now,
                       "exp": now + dt.timedelta(minutes=5)}, "attacker-key-0123456789abcdef-0123456789", algorithm="HS256")
    assert client.post(LOGOUT, headers=_auth(fake)).status_code == 401


def test_expired_token_is_401(client, store):
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    expired = jwt.encode({"sub": str(store["manager_id"]), "typ": "manager", "iat": past,
                          "exp": past + dt.timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256")
    assert client.post(LOGOUT, headers=_auth(expired)).status_code == 401


# ---------- 登録（招待コード）/ 등록(초대 코드) ----------

def test_register_hands_over_store(client, store, db):
    # 201 と {"manager_id"} は6章（旧設計）の形【提案】/ 201과 {"manager_id"}는 6장(구 설계) 모양【제안】
    code = create_invite_code(db, store["store_id"])["code"]
    new_email = _email("second")
    res = client.post(REGISTER, json={"invite_code": code, "email": new_email, "password": PASSWORD})
    assert res.status_code == 201
    new_id = res.json()["manager_id"]

    # 1トランザクションの5つが全部入ったか / 1트랜잭션의 5단계가 전부 들어갔나
    s = db.execute("SELECT manager_id FROM stores WHERE store_id = %s", (store["store_id"],)).fetchone()
    assert s["manager_id"] == new_id                                           # 店の管理者が交代 / 점장 교대
    old = db.execute("SELECT is_active FROM manager_accounts WHERE manager_id = %s",
                     (store["manager_id"],)).fetchone()
    assert old["is_active"] is False                                           # 旧は消さずに無効 / 옛 계정은 안 지우고 무효
    inv = db.execute("SELECT used_at, used_by FROM invite_codes WHERE store_id = %s",
                     (store["store_id"],)).fetchone()
    assert inv["used_at"] is not None and inv["used_by"] == new_id             # コード使用済み / 코드 사용 완료

    # 新はログインできて、旧はできない / 새 계정은 로그인 가능, 옛 계정은 불가
    assert _login(client, new_email).status_code == 200
    assert _login(client, store["email"]).status_code == 401


def test_old_managers_token_stops_working_after_handover(client, store, db):
    old_token = _token(client, store["email"])                                # 交代前に取ったトークン / 교대 전에 받은 토큰
    code = create_invite_code(db, store["store_id"])["code"]
    client.post(REGISTER, json={"invite_code": code, "email": _email("second"), "password": PASSWORD})
    # deps.py が毎回 is_active を見るので、期限前でも使えない / 매번 is_active를 보므로 만료 전이어도 못 쓴다
    assert client.post(LOGOUT, headers=_auth(old_token)).status_code == 401


def test_used_unknown_and_expired_codes_get_same_400(client, store, db):
    code = create_invite_code(db, store["store_id"])["code"]
    client.post(REGISTER, json={"invite_code": code, "email": _email("second"), "password": PASSWORD})

    expired = create_invite_code(db, store["store_id"])["code"]
    db.execute("UPDATE invite_codes SET expires_at = now() - interval '1 minute' WHERE used_at IS NULL")

    results = [
        client.post(REGISTER, json={"invite_code": c, "email": _email("x"), "password": PASSWORD})
        for c in (code, "no-such-code", expired)                              # 使用済み・存在しない・期限切れ
    ]
    assert [r.status_code for r in results] == [400, 400, 400]
    assert results[0].json() == results[1].json() == results[2].json()        # 理由を分けない / 이유 구분 안 함
    assert results[0].json()["error"]["code"] == "INVALID_CODE"


def test_existing_email_is_409_and_nothing_changes(client, store, db):
    # 409 EMAIL_EXISTS は6章（旧設計）の形【提案】/ 409 EMAIL_EXISTS는 6장(구 설계) 모양【제안】
    code = create_invite_code(db, store["store_id"])["code"]
    res = client.post(REGISTER, json={"invite_code": code, "email": store["email"], "password": PASSWORD})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "EMAIL_EXISTS"
    # 失敗したら何も変わらない（コードは未使用のまま、店の管理者もそのまま）
    # 실패하면 아무것도 안 바뀐다(코드는 미사용 그대로, 점장도 그대로)
    inv = db.execute("SELECT used_at FROM invite_codes WHERE store_id = %s", (store["store_id"],)).fetchone()
    assert inv["used_at"] is None
    s = db.execute("SELECT manager_id FROM stores WHERE store_id = %s", (store["store_id"],)).fetchone()
    assert s["manager_id"] == store["manager_id"]
    # 同じコードはまだ使える / 같은 코드는 아직 쓸 수 있다
    ok = client.post(REGISTER, json={"invite_code": code, "email": _email("second"), "password": PASSWORD})
    assert ok.status_code == 201


@pytest.mark.parametrize(
    "over",
    [
        {"email": "not-an-email"},          # @ がない / 골뱅이 없음
        {"password": "short"},              # 8文字未満【提案】/ 8자 미만【제안】
        {"password": "가" * 25},            # 75バイト > 72（bcryptの限界）/ 75바이트 > 72(bcrypt 한계)
        {"invite_code": ""},                # 空 / 빈 코드
    ],
)
def test_register_body_rules(client, over):
    body = {"invite_code": "any", "email": _email("x"), "password": PASSWORD, **over}
    res = client.post(REGISTER, json=body)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
