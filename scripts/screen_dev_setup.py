"""画面で動かす用の DB を、新しく作る(あなたの普段のDB 5433 には触らない)。
  Postgres: tests と同じ compose.test.yaml の DB(5443)に、`demo` という DB を作り直し、001+002 を当て、ダミーデータと店長アカウントを入れる。
  使い方: python -m scripts.screen_dev_setup            (メール demo@example.com / パスワード demo-password-1)
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ADMIN_URL = "postgresql://mvp:mvp@127.0.0.1:5443/mvp"
DEMO_URL = "postgresql://mvp:mvp@127.0.0.1:5443/demo"
EMAIL, PASSWORD = "demo@example.com", "demo-password-1"


def main() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3) as admin:
        admin.execute('DROP DATABASE IF EXISTS "demo" WITH (FORCE)')
        admin.execute('CREATE DATABASE "demo"')
    with psycopg.connect(DEMO_URL, autocommit=True) as c:
        for sql in sorted((ROOT / "db").glob("[0-9][0-9][0-9]_*.sql")):
            c.execute(sql.read_text(encoding="utf-8"))
    from psycopg.rows import dict_row

    from app.seed import seed_db

    with psycopg.connect(DEMO_URL, row_factory=dict_row) as c:
        res = seed_db(c, manager_email=EMAIL, password=PASSWORD)
        c.commit()
    print("DB 準備OK:", DEMO_URL)
    print("ログイン:", EMAIL, "/", PASSWORD)
    print("seed:", {k: v for k, v in res.items() if not isinstance(v, (list, dict))})


if __name__ == "__main__":
    main()
