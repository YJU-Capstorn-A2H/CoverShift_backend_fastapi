"""하네스 자체 시험: 시험 DB 가 추천안(001) + 로컬 확장(002) 표로 만들어지고, LLM 롤이 이름을 못 읽는다."""
import psycopg
import pytest

from app.mcp_tools import get_staff_info
from tests.conftest import TABLES


@pytest.mark.req("A-18")
def test_schema_and_llm_role_hides_name(conn):
    rows = conn.execute("select table_name from information_schema.tables where table_schema='public'").fetchall()
    # 001 의 15개 + 002 의 2개 = 17개. TABLES(테스트마다 비우는 목록)에서 표가 빠져도 여기서 잡힌다
    assert sorted(r[0] for r in rows) == sorted(TABLES) and len(TABLES) == 17
    conn.execute("insert into manager_accounts(email,password_hash) values ('a@x','h')")
    conn.execute("insert into stores(manager_id,name) values (1,'s')")
    conn.execute("insert into employees(store_id,name,is_regular) values (1,'실명',true)")
    conn.execute("insert into shift_periods(store_id,target_month,submission_deadline,designated_off_days) "
                 "values (1,'2026-11-01','2026-10-20T00:00+09',9)")
    conn.commit()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("set role covershift_llm_read; select name from employees")
    conn.rollback()
    info = get_staff_info(1)
    assert info["employees"][0]["employee_id"] == 1
    assert "실명" not in repr(info) and "name" not in info["employees"][0]
