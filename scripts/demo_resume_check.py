"""재시작 복구 확인 (D-3): 지금 RunStatus 를 찍는다. 읽기만 한다 (resume 하지 않는다).

  uv run --env-file .env python -m scripts.demo_resume_check --period-id 1

시연 순서 예: demo_prepare → `docker compose -p mvp-demo restart aegra api` → 이 스크립트 → state 가 waiting_review 그대로인지 본다.
RUNNER=aegra 이면 그 thread 의 Aegra run 목록도 찍는다 — run 이 'running' 으로 남아 있는데 아무것도 돌지 않으면
Redis 없는 Aegra 가 run 도중 강제 종료된 경우다 (11/12 로컬 프로세스 실측: SIGKILL → 'running' 으로 남음, SIGTERM → 'interrupted').
종료 코드: waiting_review 0 / 그 밖 1 / 기간 · thread 없음 2.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import connect  # noqa: E402


def thread_of(period_id: int, database_url: str | None = None) -> str | None:
    with connect(database_url) as c:
        row = c.execute("SELECT thread_id FROM shift_periods WHERE period_id = %s", (period_id,)).fetchone()
    if row is None:
        raise LookupError(f"shift_periods 에 period_id={period_id} 가 없습니다")
    return row["thread_id"]


def check(period_id: int, runner, database_url: str | None = None) -> dict:
    return runner.status(period_id, thread_of(period_id, database_url))


def aegra_runs(aegra_url: str, thread_id: str) -> list[dict]:
    from langgraph_sdk import get_sync_client

    c = get_sync_client(url=aegra_url, api_key=None)
    return [{k: r.get(k) for k in ("run_id", "status", "created_at", "updated_at")} for r in c.runs.list(thread_id, limit=10)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="재시작 뒤 승인 대기가 남아 있는지 본다 (D-3)")
    ap.add_argument("--period-id", type=int, required=True)
    a = ap.parse_args(argv)
    s = get_settings()
    try:
        tid = thread_of(a.period_id, s.database_url)
    except LookupError as e:
        print(e, file=sys.stderr)
        return 2
    if not tid:
        print(f"period {a.period_id} 는 아직 thread 가 없습니다 (편성 시작 전)", file=sys.stderr)
        return 2
    from app.runner import get_runner

    st = get_runner().status(a.period_id, tid)
    print(json.dumps(st, ensure_ascii=False, indent=2, default=str))
    if s.runner == "aegra":
        print("[aegra runs]", json.dumps(aegra_runs(s.aegra_url, tid), ensure_ascii=False, default=str))
    return 0 if st["state"] == "waiting_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
