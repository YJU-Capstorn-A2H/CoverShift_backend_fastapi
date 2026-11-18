"""운영 스크립트: 최초 점포 + 첫 관리자 (A-4, V9_ko 44 「A. 최초 1명: 운영(스크립트)」, V9_ko 431).

실행: printf '%s\\n' "$PW" | uv run python -m ops.create_store --store "<점포명>" --email <e> --password-stdin
- 비밀번호는 표준 입력(첫 줄)으로만 받는다 — 명령줄 인자로 받으면 셸 기록 · ps 에 남는다.
- 접속 대상은 DATABASE_URL (app.config). .env 를 스스로 읽지 않는다 — 셸에서 넘긴다.
"""
from __future__ import annotations

import argparse
import sys

import psycopg

from app.db import connect
from app.repo import managers


def main(argv: list[str] | None = None, stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    ap = argparse.ArgumentParser(prog="ops.create_store", description="최초 점포 + 첫 관리자 등록")
    ap.add_argument("--store", required=True, help="점포 이름")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password-stdin", action="store_true", required=True, help="비밀번호를 표준 입력 첫 줄에서 읽는다")
    a = ap.parse_args(argv)
    password = stdin.readline().rstrip("\r\n")
    # API 의 등록(RegisterIn)과 같은 규칙 (가정 신규: 8자 이상 · 72바이트 이하)
    if len(password) < 8 or len(password.encode()) > 72:
        print("오류: 비밀번호는 8자 이상 · 72바이트 이하여야 합니다", file=sys.stderr)
        return 2
    try:
        with connect() as c:  # 한 트랜잭션: 둘 다 들어가거나 둘 다 안 들어간다
            r = managers.create_store(c, store_name=a.store, email=a.email, password=password)
    except psycopg.errors.UniqueViolation:
        print("오류: 이미 등록된 이메일입니다", file=sys.stderr)
        return 1
    print(f"store_id={r['store_id']} manager_id={r['manager_id']}", file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
