"""오류 응답 모양 (A-19, V9_ko 977): 어떤 길로 나든 {"error": {"code", "message"}}."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app

from ._util import err


@pytest.mark.req("A-19")
def test_router_validation_and_db_errors_share_one_shape(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    assert err(api.c.get("/api/no-such-path", headers=h)) == (404, "NOT_FOUND")
    assert err(api.c.put("/api/store", headers=h, json={})) == (405, "METHOD_NOT_ALLOWED")
    assert err(api.c.get("/api/employees/abc", headers=h)) == (422, "VALIDATION_ERROR")
    assert err(api.c.post("/api/employees", headers=h, content=b"{not json", )) == (422, "VALIDATION_ERROR")
    assert err(api.c.post("/api/employees", headers=h, json={"name": "x", "is_regular": True,
                                                             "weekly_days": 9})) == (422, "CHECK_VIOLATION")
    assert err(api.c.get("/api/store")) == (401, "UNAUTHORIZED")


@pytest.mark.req("A-19")
def test_unexpected_error_is_500_with_same_shape_and_no_internal_detail(two_stores):
    api = two_stores
    h = api.stores["A"]["h"]
    pid = api.period(h)
    api.c.post(f"/api/shift-periods/{pid}/start-now", headers=h)
    api.runner.raise_on["status"] = RuntimeError("secret internal detail")
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(f"/api/shift-periods/{pid}/run-status", headers=h)
    assert err(r) == (500, "INTERNAL_ERROR")
    assert "secret internal detail" not in r.text


@pytest.mark.req("A-2", "A-19")
def test_empty_jwt_secret_refuses_to_sign_or_verify(two_stores, monkeypatch):
    api = two_stores
    monkeypatch.setenv("JWT_SECRET", "")  # .env.example 의 빈 값을 그대로 쓴 경우
    r = api.c.post("/api/auth/manager/login", json={"email": "a@store.test", "password": "password-123"})
    assert err(r) == (500, "SERVER_MISCONFIGURED")
    assert err(api.c.get("/api/auth/me", headers=api.stores["A"]["h"])) == (500, "SERVER_MISCONFIGURED")


@pytest.mark.req("A-9")
def test_period_screens_work_even_if_runner_cannot_be_built(two_stores):
    """러너(그래프) 준비가 실패해도 thread 가 없는 기간 화면은 열린다. thread 가 있으면 run_state="error"."""
    from app.runner import get_runner

    api = two_stores
    h = api.stores["A"]["h"]
    built = []

    def broken():
        built.append(1)
        raise RuntimeError("graph not ready")

    app.dependency_overrides[get_runner] = broken
    pid = api.period(h)
    assert api.c.get("/api/shift-periods", headers=h).json()[0]["run_state"] == "not_started"
    assert built == []  # 필요 없으면 만들지도 않는다
    api.sql("UPDATE shift_periods SET thread_id = %s", (f"period-{pid}",))
    assert api.c.get(f"/api/shift-periods/{pid}", headers=h).json()["run_state"] == "error"


@pytest.mark.req("A-1")
def test_db_dependency_commits_before_response():
    """요청 트랜잭션은 응답 전에 commit 되어야 한다 (FastAPI Depends scope="function"). 설정이 빠지면 여기서 걸린다."""
    import typing

    from app.api.deps import Conn, get_conn

    dep = typing.get_args(Conn)[1]
    assert dep.dependency is get_conn and dep.scope == "function"
