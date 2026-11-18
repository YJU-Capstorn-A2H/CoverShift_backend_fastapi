"""운영 스크립트: 관리자 교대용 초대 코드 발급 (A-4, V9_ko 155 「운영자가 점포를 지정해 발급 · 1회용 · 유효 1일」).

실행: uv run python -m ops.issue_invite --store-id <id>
- 코드 원문은 여기서 **한 번만** 출력한다. DB 에는 sha256 해시만 남는다 (V9_ko 46, 433 → app/repo/managers.py 주석).
- 잃어버리면 다시 발급한다(이전 코드는 기한까지 살아 있다 — 지우는 기능은 없다).
"""
from __future__ import annotations

import argparse
import sys

from app.db import connect
from app.repo import managers


def main(argv: list[str] | None = None, stdout=None) -> int:
    stdout = stdout or sys.stdout
    ap = argparse.ArgumentParser(prog="ops.issue_invite", description="초대 코드 발급 (1회용 · 1일)")
    ap.add_argument("--store-id", type=int, required=True)
    a = ap.parse_args(argv)
    try:
        with connect() as c:
            code, expires_at = managers.issue_invite(c, a.store_id)
    except LookupError:
        print(f"오류: store_id={a.store_id} 점포가 없습니다", file=sys.stderr)
        return 1
    print(f"invite_code={code}", file=stdout)
    print(f"expires_at={expires_at.isoformat()}", file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
