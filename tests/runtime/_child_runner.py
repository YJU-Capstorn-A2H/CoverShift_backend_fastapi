"""재시작 · 강제 종료 시험용 자식 프로세스. `python -m tests.runtime._child_runner <db_url> <thread_id> <mode>`

mode
  to_waiting : start → 승인 대기까지 → status 를 JSON 한 줄로 찍고 정리 없이 종료 (os._exit = 프로세스가 내려간 것과 같게)
  slow       : prepare 가 60초 걸리는 그래프로 start → "STARTED" 를 찍고 기다린다 (부모가 kill -9)
  fail       : prepare 에서 예외 → error 가 될 때까지 기다린 뒤 status 를 찍고 종료
"""
from __future__ import annotations

import json
import os
import sys
import time

from app.runner import LocalRunner
from tests.runtime import dummy_graph


def main(db_url: str, thread_id: str, mode: str) -> None:
    kw = {"slow": {"prepare_delay": 60.0}, "fail": {"fail_in": "prepare"}}.get(mode, {})
    r = LocalRunner(db_url, graph_factory=dummy_graph.make_factory(**kw))
    r.start(1, thread_id)
    if mode == "slow":
        print("STARTED", flush=True)
        time.sleep(120)
        os._exit(3)
    target = {"to_waiting": "waiting_review", "fail": "error"}[mode]
    deadline = time.monotonic() + 20
    st = r.status(1, thread_id)
    while st["state"] != target and time.monotonic() < deadline:
        time.sleep(0.05)
        st = r.status(1, thread_id)
    print(json.dumps(st, ensure_ascii=False, default=str), flush=True)
    os._exit(0 if st["state"] == target else 2)


if __name__ == "__main__":
    main(*sys.argv[1:4])
