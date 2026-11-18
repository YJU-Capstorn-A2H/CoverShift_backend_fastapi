"""시연 준비 (D-2, V9_ko 456): 편성을 「승인 대기」까지 미리 돌려 체크포인트에 남긴다. 발표 때는 화면에서 승인(resume)만 한다.

  uv run --env-file .env python -m scripts.demo_prepare --period-id 1
  printf '%s' "$PW" | uv run --env-file .env python -m scripts.demo_prepare --seed --email m@example.com --password-stdin
      # ↑ 빈 DB 에 app.seed.seed_db 로 더미 데이터(점장 계정 포함)부터 넣는다. 비밀번호는 stdin 으로만 받는다

- 러너는 설정(RUNNER=local|aegra)을 따른다. 시연 스택이면 RUNNER=aegra · AEGRA_URL=http://127.0.0.1:2026 ·
  DATABASE_URL=...@127.0.0.1:5444/... (compose.yaml 의 db).
- shift_periods.thread_id 가 비어 있으면 새 thread_id(uuid4)를 적는다 (기간 1개 = thread 1개, F-10).
  (가정 신규) thread_id 형식 = uuid4 문자열. 입구 API 가 다른 형식을 쓰더라도 이미 적힌 값이 있으면 그 값을 그대로 쓴다.
- 이미 승인 대기면 다시 돌리지 않고 그대로 보여 준다 (몇 번 불러도 같다). 확정 · 오류면 0 이 아닌 값으로 끝난다.
- 이 스크립트는 .env 를 직접 읽지 않는다. 값은 `uv run --env-file .env` 가 넣는다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

if __package__ in (None, ""):  # `python scripts/demo_prepare.py` 로 불려도 app 을 찾게
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import connect  # noqa: E402

EXIT = {"waiting_review": 0, "waiting_contact": 3, "confirmed": 4, "error": 5, "timeout": 6}


def ensure_thread(period_id: int, database_url: str | None = None) -> str:
    """기간의 thread_id. 없으면 uuid4 를 적는다. 동시에 두 번 불려도 하나만 적힌다 (WHERE thread_id IS NULL)."""
    with connect(database_url) as c:
        row = c.execute("SELECT thread_id FROM shift_periods WHERE period_id = %s", (period_id,)).fetchone()
        if row is None:
            raise LookupError(f"shift_periods 에 period_id={period_id} 가 없습니다")
        if row["thread_id"]:
            return row["thread_id"]
        c.execute("UPDATE shift_periods SET thread_id = %s WHERE period_id = %s AND thread_id IS NULL",
                  (str(uuid.uuid4()), period_id))
        c.commit()
        return c.execute("SELECT thread_id FROM shift_periods WHERE period_id = %s", (period_id,)).fetchone()["thread_id"]


def prepare(period_id: int, runner, database_url: str | None = None, *, timeout: float = 1800.0,
            poll: float = 2.0, out=print) -> dict:
    """승인 대기까지 돌린다. 돌려주는 값 = 마지막 RunStatus (+ 시간 초과면 state="timeout")."""
    tid = ensure_thread(period_id, database_url)
    st = runner.status(period_id, tid)
    out(f"[prepare] period={period_id} thread={tid} 지금 상태={st['state']}")
    if st["state"] == "not_started":
        st = runner.start(period_id, tid)
        out("[prepare] 편성 시작 (백그라운드)")
    t0 = time.monotonic()
    last = None
    while st["state"] == "running":
        if time.monotonic() - t0 > timeout:
            return {**st, "state": "timeout", "error": f"{timeout:.0f}초 안에 승인 대기가 되지 않음"}
        if st["state"] != last:
            out(f"[prepare] {time.monotonic() - t0:6.1f}s running …")
            last = st["state"]
        time.sleep(poll)
        st = runner.status(period_id, tid)
    return st


def summary(st: dict) -> dict:
    w = st.get("waiting") or {}
    return {"period_id": st.get("period_id"), "thread_id": st.get("thread_id"), "state": st.get("state"),
            "draft_id": w.get("draft_id"), "result": w.get("result"),
            "violations": len(w.get("violations") or []), "auto_retry_count": w.get("auto_retry_count"),
            "error": st.get("error")}


def _seed(database_url: str, email: str, password: str) -> int:
    try:
        from app.seed import seed_db  # 다른 에이전트 소유: seed_db(conn, path=None, *, manager_email, password) -> dict
    except ImportError as e:
        raise SystemExit(f"app.seed.seed_db 를 import 하지 못했습니다: {e}") from e
    with connect(database_url) as c:
        res = seed_db(c, manager_email=email, password=password)
        c.commit()
    return int(res["period_id"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시연 준비: 승인 대기까지 미리 돌린다 (D-2)")
    ap.add_argument("--period-id", type=int)
    ap.add_argument("--seed", action="store_true", help="app.seed.seed_db 로 더미 데이터를 먼저 넣는다 (빈 DB 에서만)")
    ap.add_argument("--email", help="--seed 때 만들 점장 계정 메일")
    ap.add_argument("--password-stdin", action="store_true", help="--seed 때 점장 비밀번호를 stdin 에서 읽는다")
    ap.add_argument("--timeout", type=float, default=1800.0, help="승인 대기까지 기다리는 최대 초 (기본 1800)")
    ap.add_argument("--poll", type=float, default=2.0)
    a = ap.parse_args(argv)
    s = get_settings()
    period_id = a.period_id
    if a.seed:
        if not (a.email and a.password_stdin):
            ap.error("--seed 에는 --email 과 --password-stdin 이 필요합니다 (비밀번호를 인자로 받지 않는다)")
        seeded = _seed(s.database_url, a.email, sys.stdin.readline().rstrip("\n"))
        print(f"[prepare] seed 완료 period_id={seeded}")
        period_id = seeded if period_id is None else period_id
    if period_id is None:
        ap.error("--period-id 또는 --seed 가 필요합니다")
    from app.runner import get_runner

    print(f"[prepare] RUNNER={s.runner} LLM_MODE={s.llm_mode}")
    st = prepare(period_id, get_runner(), s.database_url, timeout=a.timeout, poll=a.poll)
    print(json.dumps(summary(st), ensure_ascii=False, indent=2))
    if st["state"] != "waiting_review":
        print(f"[prepare] 승인 대기가 아닙니다: {st['state']} {st.get('error') or ''}", file=sys.stderr)
    return EXIT.get(st["state"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
