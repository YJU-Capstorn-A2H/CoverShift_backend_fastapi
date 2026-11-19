"""하네스 자체 시험: 시험 DB 가 v17 + (가정) 표로 만들어지고, LLM 롤이 이름을 못 읽는다."""
import psycopg
import pytest

from app.mcp_tools import get_staff_info


@pytest.mark.req("A-18")
def test_schema_and_llm_role_hides_name(conn):
    n = conn.execute("select count(*) from information_schema.tables where table_schema='public'").fetchone()[0]
    assert n == 18
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
