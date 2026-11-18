"""인증 (M-4, A-1 ~ A-4, A-14). 규칙마다 지키는 경우와 일부러 깨뜨리는 경우를 같이 본다."""
from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.repo import managers
from ops import issue_invite

from ._util import PW, err


def _issue(store_id: int) -> str:
    buf = io.StringIO()
    assert issue_invite.main(["--store-id", str(store_id)], stdout=buf) == 0
    return buf.getvalue().split("invite_code=")[1].split()[0]


@pytest.mark.req("A-1", "A-3", "M-4")
def test_register_hands_over_store_and_old_jwt_is_rejected(api):
    a = api.make_store("A", "아즈키점", "old@store.test")
    assert api.c.get("/api/auth/me", headers=a["h"]).status_code == 200
    code = _issue(a["store_id"])

    r = api.c.post("/api/auth/register", json={"invite_code": code, "email": "New@Store.test", "password": PW})
    assert r.status_code == 201, r.text
    new_id = r.json()["manager_id"]

    # 한 트랜잭션의 결과 4가지 (V9_ko 149)
    assert api.sql("SELECT manager_id FROM stores WHERE store_id = %s", (a["store_id"],)) == [{"manager_id": new_id}]
    assert api.sql("SELECT is_active FROM manager_accounts WHERE manager_id = %s",
                   (a["manager_id"],)) == [{"is_active": False}]
    inv = api.sql("SELECT used_by, used_at IS NOT NULL AS used FROM invite_codes")
    assert inv == [{"used_by": new_id, "used": True}]

    # 깨뜨리는 쪽: 구 관리자의 JWT 는 기한 안이어도 거부 (G15), 구 계정으로 로그인도 거부
    assert err(api.c.get("/api/auth/me", headers=a["h"])) == (401, "UNAUTHORIZED")
    assert err(api.c.post("/api/auth/manager/login", json={"email": "old@store.test", "password": PW})) == (
        401, "INVALID_CREDENTIALS")
    # 새 관리자는 같은 점포로 들어간다 (이메일은 소문자로 맞춘다)
    h = api.login("new@store.test")
    me = api.c.get("/api/auth/me", headers=h).json()
    assert me == {"manager_id": new_id, "email": "new@store.test", "store_id": a["store_id"], "store_name": "아즈키점"}


@pytest.mark.req("A-1")
def test_register_bad_code_is_one_400_for_unknown_expired_used(api):
    a = api.make_store("A", "아즈키점", "old@store.test")
    expired = "expired-code-1234"
    api.sql("INSERT INTO invite_codes (code, store_id, expires_at) VALUES (%s, %s, now() - interval '1 second')",
            (managers.hash_code(expired), a["store_id"]))
    used = _issue(a["store_id"])
    assert api.c.post("/api/auth/register", json={"invite_code": used, "email": "n1@x.test",
                                                  "password": PW}).status_code == 201

    bodies = []
    for i, code in enumerate(["no-such-code", expired, used]):
        r = api.c.post("/api/auth/register", json={"invite_code": code, "email": f"n{i + 2}@x.test", "password": PW})
        assert err(r) == (400, "INVALID_CODE")
        bodies.append(r.json())
    assert bodies[0] == bodies[1] == bodies[2]  # 사유를 구분하지 않는다 (V9_ko 57)
    assert api.sql("SELECT count(*) AS n FROM manager_accounts")[0]["n"] == 2  # 실패한 등록은 계정을 남기지 않는다


@pytest.mark.req("A-1")
def test_register_email_exists_409_and_code_stays_unused(api):
    a = api.make_store("A", "아즈키점", "old@store.test")
    code = _issue(a["store_id"])
    r = api.c.post("/api/auth/register", json={"invite_code": code, "email": "OLD@store.test", "password": PW})
    assert err(r) == (409, "EMAIL_EXISTS")
    # 트랜잭션이 통째로 취소: 코드 미사용 · 구 관리자 그대로
    assert api.sql("SELECT used_at FROM invite_codes") == [{"used_at": None}]
    assert api.sql("SELECT manager_id FROM stores") == [{"manager_id": a["manager_id"]}]
    assert api.c.post("/api/auth/register", json={"invite_code": code, "email": "new@x.test",
                                                  "password": PW}).status_code == 201


@pytest.mark.req("A-1", "A-19")
def test_register_rejects_weak_password_and_bad_email(api):
    api.make_store("A", "아즈키점", "old@store.test")
    assert err(api.c.post("/api/auth/register", json={"invite_code": "x", "email": "n@x.test",
                                                      "password": "short"})) == (422, "VALIDATION_ERROR")
    assert err(api.c.post("/api/auth/register", json={"invite_code": "x", "email": "not-an-email",
                                                      "password": PW})) == (422, "VALIDATION_ERROR")
    assert err(api.c.post("/api/auth/register", json={"invite_code": "x", "email": "n@x.test",
                                                      "password": "あ" * 30})) == (422, "VALIDATION_ERROR")  # 90바이트


@pytest.mark.req("A-2", "M-4")
def test_login_success_claims_and_same_401_for_wrong_password_or_unknown_email(api):
    a = api.make_store("A", "아즈키점", "a@store.test")
    r = api.c.post("/api/auth/manager/login", json={"email": "a@store.test", "password": PW})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"access_token", "manager_id", "store_id"}
    claims = jwt.decode(body["access_token"], "test-only-secret-0123456789abcdef", algorithms=["HS256"])
    assert claims["sub"] == str(a["manager_id"]) and claims["store_id"] == a["store_id"] and "exp" in claims
    # bcrypt 로 저장됐는가 (평문 아님)
    h = api.sql("SELECT password_hash FROM manager_accounts")[0]["password_hash"]
    assert h.startswith("$2") and PW not in h

    wrong = api.c.post("/api/auth/manager/login", json={"email": "a@store.test", "password": "wrong-password"})
    unknown = api.c.post("/api/auth/manager/login", json={"email": "nobody@store.test", "password": PW})
    assert err(wrong) == err(unknown) == (401, "INVALID_CREDENTIALS")
    assert wrong.json() == unknown.json()


@pytest.mark.req("A-3", "A-14")
def test_manager_api_rejects_missing_bad_expired_or_forged_jwt(api):
    a = api.make_store("A", "아즈키점", "a@store.test")
    now = datetime.now(UTC)

    def tok(secret="test-only-secret-0123456789abcdef", **claims):
        base = {"sub": str(a["manager_id"]), "store_id": a["store_id"], "exp": now + timedelta(minutes=5)}
        return {"Authorization": "Bearer " + jwt.encode({**base, **claims}, secret, algorithm="HS256")}

    assert api.c.get("/api/auth/me", headers=tok()).status_code == 200  # 지키는 경우
    cases = [
        {},  # 헤더 없음
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Basic abc"},
        tok(exp=now - timedelta(seconds=1)),  # 기한 지남
        tok(secret="other-secret-0123456789abcdefghij"),  # 서명 위조
        tok(store_id=a["store_id"] + 99),  # 점포 클레임 불일치
        tok(sub="999"),  # 없는 관리자
    ]
    for h in cases:
        assert err(api.c.get("/api/auth/me", headers=h)) == (401, "UNAUTHORIZED"), h
    # 계정을 무효로 바꾸면 같은 토큰이 바로 거부된다 (요청마다 is_active 확인, G15)
    api.sql("UPDATE manager_accounts SET is_active = false WHERE manager_id = %s", (a["manager_id"],))
    assert err(api.c.get("/api/auth/me", headers=tok())) == (401, "UNAUTHORIZED")


@pytest.mark.req("A-14")
def test_dev_skip_auth_uses_manager_1_only_without_header(api, monkeypatch):
    api.make_store("A", "아즈키점", "a@store.test")
    assert err(api.c.get("/api/auth/me")) == (401, "UNAUTHORIZED")  # 기본은 꺼짐
    monkeypatch.setenv("DEV_SKIP_AUTH", "true")
    me = api.c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["manager_id"] == 1
    # 헤더가 있으면 건너뛰지 않고 끝까지 검사한다
    assert err(api.c.get("/api/auth/me", headers={"Authorization": "Bearer junk"})) == (401, "UNAUTHORIZED")


@pytest.mark.req("A-2")
def test_logout_is_204_and_needs_manager(api):
    a = api.make_store("A", "아즈키점", "a@store.test")
    assert api.c.post("/api/auth/logout", headers=a["h"]).status_code == 204
    assert err(api.c.post("/api/auth/logout")) == (401, "UNAUTHORIZED")
