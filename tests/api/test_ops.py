"""운영 스크립트 (A-4): 최초 점포 + 관리자, 초대 코드 발급."""
from __future__ import annotations

import io

import pytest

from app.repo import managers
from ops import create_store, issue_invite

from ._util import PW


@pytest.mark.req("A-4", "M-4")
def test_create_store_reads_password_from_stdin_and_login_works(api):
    out = io.StringIO()
    rc = create_store.main(["--store", "아즈키점", "--email", "Owner@Store.test", "--password-stdin"],
                           stdin=io.StringIO(PW + "\n"), stdout=out)
    assert rc == 0
    assert "store_id=1 manager_id=1" in out.getvalue()
    assert PW not in out.getvalue()
    row = api.sql("SELECT m.email, m.password_hash, s.name FROM stores s JOIN manager_accounts m USING (manager_id)")
    assert row[0]["email"] == "owner@store.test" and row[0]["name"] == "아즈키점"
    assert row[0]["password_hash"] != PW and managers.check_password(PW, row[0]["password_hash"])
    api.login("owner@store.test")


@pytest.mark.req("A-4")
def test_create_store_rejects_short_password_and_duplicate_email(api):
    rc = create_store.main(["--store", "s", "--email", "a@x.test", "--password-stdin"], stdin=io.StringIO("short\n"),
                           stdout=io.StringIO())
    assert rc == 2
    assert api.sql("SELECT count(*) AS n FROM manager_accounts")[0]["n"] == 0
    args = ["--store", "s", "--email", "a@x.test", "--password-stdin"]
    assert create_store.main(args, stdin=io.StringIO(PW + "\n"), stdout=io.StringIO()) == 0
    assert create_store.main(args, stdin=io.StringIO(PW + "\n"), stdout=io.StringIO()) == 1
    assert api.sql("SELECT count(*) AS n FROM stores")[0]["n"] == 1  # 두 번째는 점포도 안 생김(한 트랜잭션)


@pytest.mark.req("A-4")
def test_issue_invite_prints_code_once_and_stores_only_hash_for_one_day(api):
    a = api.make_store("A", "아즈키점", "a@store.test")
    out = io.StringIO()
    assert issue_invite.main(["--store-id", str(a["store_id"])], stdout=out) == 0
    code = out.getvalue().split("invite_code=")[1].split()[0]
    row = api.sql("""SELECT code, store_id, used_at,
                            extract(epoch FROM expires_at - now()) AS ttl FROM invite_codes""")[0]
    assert row["code"] == managers.hash_code(code) and row["code"] != code  # DB 에는 해시만 (V9_ko 46, 433)
    assert row["store_id"] == a["store_id"] and row["used_at"] is None
    assert 86400 - 60 < float(row["ttl"]) <= 86400  # 1일
    # 깨뜨리는 쪽: 없는 점포
    assert issue_invite.main(["--store-id", "999"], stdout=io.StringIO()) == 1
    assert api.sql("SELECT count(*) AS n FROM invite_codes")[0]["n"] == 1
