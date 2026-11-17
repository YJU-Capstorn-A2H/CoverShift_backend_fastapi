# node/db.py -- DB(PostgreSQL)から、編成に必要なデータを読むファイル(読むだけ。書き込みはしない)
#   読んだ行は node/convert.py でグラフ用のデータに変える。ここは「どのテーブルから何を読むか」だけ
#   使うテーブル: shift_periods / store_time_rules / employees / availability_submissions
import datetime
import psycopg
from psycopg.rows import dict_row
from node.convert import make_period, make_staff_list, make_store_settings


def load_period_data(db_url, period_id):
    """period_id(shift_periods の行)の1か月分に必要なデータを、まとめて読む。
    戻り値: {"store_id", "period", "store_settings", "staff_list"}  (名前は読まない。staff_list の staff_name は None)
    読んでいる間に他の人が書き換えても、同じ時点のデータだけを見るようにする(REPEATABLE READ・読み取り専用)"""
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True

        period_row = conn.execute(
            "SELECT period_id, store_id, target_month, designated_off_days FROM shift_periods WHERE period_id = %s",
            (period_id,)).fetchone()
        if period_row is None:
            raise ValueError(f"period_id={period_id} が shift_periods にありません")
        store_id = period_row["store_id"]
        period = make_period(period_row["target_month"])
        first_day = period_row["target_month"]
        next_month_first_day = first_day + datetime.timedelta(days=period["days"])

        rule_rows = conn.execute(
            "SELECT weekday, start_time, end_time, min_headcount, min_qualified FROM store_time_rules "
            "WHERE store_id = %s ORDER BY weekday, start_time", (store_id,)).fetchall()
        employee_rows = conn.execute(
            "SELECT employee_id, is_regular, has_qualification, closing_ok FROM employees "    # name は読まない(名前を見られないロールで読むため。基準文書 11/07)
            "WHERE store_id = %s ORDER BY employee_id", (store_id,)).fetchall()
        submission_rows = conn.execute(
            "SELECT a.employee_id, a.work_date, a.kind, a.start_time, a.end_time FROM availability_submissions a "
            "JOIN employees e ON e.employee_id = a.employee_id "
            "WHERE e.store_id = %s AND a.work_date >= %s AND a.work_date < %s",
            (store_id, first_day, next_month_first_day)).fetchall()

    if not employee_rows:
        raise ValueError(f"store_id={store_id} の従業員が employees にいません")
    if not rule_rows:
        print(f"【注意】store_id={store_id} の時間帯ルール(store_time_rules)がありません。人数・資格者の検査は何も見ません。")
    return {
        "store_id": store_id,
        "period": period,
        "store_settings": make_store_settings(rule_rows, period_row),
        "staff_list": make_staff_list(employee_rows, submission_rows),
    }

