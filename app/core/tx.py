# app/core/tx.py
# 既存の app/core/db.py（open_pool / close_pool / pool / get_conn）はそのまま使い、
# 「1リクエスト=1トランザクション」と「同じ月の同時操作ロック」だけをここに足す。
# 기존 app/core/db.py(open_pool / close_pool / pool / get_conn)는 그대로 쓰고,
# 「1요청=1트랜잭션」과 「같은 달의 동시 조작 락」만 여기에 추가한다.
from contextlib import contextmanager

from app.core import db as _db


@contextmanager
def tx():
    """正常に抜ければ commit、例外なら rollback。プールが未起動なら、ここで開く（テスト用）。
    정상 종료하면 commit, 예외면 rollback. 풀이 기동 전이면 여기서 연다(테스트용)."""
    if _db.pool is None:
        _db.open_pool()
    with _db.pool.connection() as conn:     # psycopg_pool が commit / rollback を行う
        yield conn


def try_lock_period(conn, period_id: int) -> bool:
    """同じ編成期間(=thread)への同時操作を1つだけ通す（基準文書 D4）。
    トランザクションが終わると自動で外れる。取れなければ False。
    같은 편성 기간(=thread)에 대한 동시 조작을 하나만 통과시킨다(기준 문서 D4).
    트랜잭션이 끝나면 자동으로 풀린다. 못 잡으면 False."""
    row = conn.execute("SELECT pg_try_advisory_xact_lock(7001, %s) AS ok", (period_id,)).fetchone()
    return bool(row["ok"])
