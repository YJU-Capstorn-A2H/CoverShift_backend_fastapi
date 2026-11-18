"""AegraRunner ↔ 진짜 Aegra 0.10.8 서버 (같은 venv 의 aegra_api 를 uvicorn 으로 직접 띄움, Redis 없음 = 프로세스 안 실행).

- 그래프는 tests/runtime/dummy_graph.py (aegra_e2e.json). 진짜 편성 그래프 · LLM 은 쓰지 않는다.
- Aegra 의 표 · 체크포인트는 시험 Postgres(5443)에 새로 만든 DB `t_aegra_<임의>` 에 두고 끝나면 지운다.
- `aegra serve` 대신 uvicorn 을 직접 띄운다: aegra serve 를 죽여도 uvicorn 자식이 살아남는 문제
  (prototype_skeleton 결함 7) 때문. 시험 설정 파일이 tests/runtime 에 있어 저장소의 .env 를 읽지 않는다.
- 서버가 안 뜨면 건너뛰지 않고 실패한다 (같은 venv 의 패키지와 시험 DB 만 쓰므로 안 뜨는 것 자체가 문제).
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import psycopg
import pytest
from langgraph_sdk import get_sync_client

from app.runner import AegraRunner, Busy, NotWaiting
from tests.conftest import ADMIN_URL, _url_for
from tests.runtime.conftest import new_thread, wait_state

HERE = Path(__file__).resolve().parent
APPROVE = {"decision": "approved", "manager_id": 1, "draft_id": 1, "waived_keys": []}


class AegraServer:
    def __init__(self, db_url: str, log_path: Path):
        self.db_url = db_url
        self.port = self._free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def start(self, timeout: float = 90.0) -> None:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "AEGRA_", "REDIS_"))}
        env.update(DATABASE_URL=self.db_url, AEGRA_CONFIG=str(HERE / "aegra_e2e.json"), AUTH_TYPE="noop",
                   REDIS_BROKER_ENABLED="false", CRON_ENABLED="false", ENV_MODE="LOCAL", LOG_LEVEL="WARNING",
                   DUMMY_REVIEW_DELAY="0.5", PYTHONPATH=str(HERE))
        log = open(self.log_path, "a")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "aegra_api.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"{self.url}/health", timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.3)
        tail = self.log_path.read_text()[-3000:]
        self.stop()
        pytest.fail(f"Aegra 서버가 {timeout}초 안에 뜨지 않음. 로그 끝:\n{tail}")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(5)
        self.proc = None


@pytest.fixture(scope="module")
def aegra(db_url, tmp_path_factory):
    name = f"t_aegra_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    srv = AegraServer(_url_for(name), tmp_path_factory.mktemp("aegra") / "aegra.log")
    srv.start()
    yield srv
    srv.stop()
    with psycopg.connect(ADMIN_URL, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _runner(aegra, db_url, assistant="shift") -> AegraRunner:
    return AegraRunner(aegra.url, db_url, assistant_id=assistant, poll_interval=0.1)


def _log(aegra, tid) -> list:
    return get_sync_client(url=aegra.url, api_key=None).threads.get_state(tid)["values"].get("log", [])


@pytest.mark.req("F-11")
@pytest.mark.req("D-1")
def test_aegra_start_wait_resume_confirm(aegra, db_url):
    r = _runner(aegra, db_url)
    tid = new_thread()
    assert r.status(1, tid)["state"] == "not_started"
    assert r.start(1, tid)["state"] == "running"
    st = wait_state(r, 1, tid, {"waiting_review"}, timeout=30)
    assert st["waiting"]["type"] == "review" and st["waiting"]["draft_id"] == 1
    with pytest.raises(NotWaiting):
        r.resume(1, tid, {"contact_id": 7, "answer": "yes"})
    r.resume(1, tid, APPROVE)
    wait_state(r, 1, tid, {"confirmed"}, timeout=30)
    assert _log(aegra, tid).count("commit") == 1
    with pytest.raises(NotWaiting):
        r.resume(1, tid, APPROVE)
    r.close()


@pytest.mark.req("A-15")
def test_aegra_concurrent_resume_two_entrances_one_proceeds(aegra, db_url):
    a, b = _runner(aegra, db_url), _runner(aegra, db_url)
    tid = new_thread()
    a.start(1, tid)
    wait_state(a, 1, tid, {"waiting_review"}, timeout=30)
    barrier = threading.Barrier(2)
    out: list = [None, None]

    def go(i, r):
        barrier.wait()
        try:
            out[i] = r.resume(1, tid, APPROVE)
        except Exception as e:
            out[i] = e

    ts = [threading.Thread(target=go, args=(i, r)) for i, r in enumerate((a, b))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert sorted(type(o).__name__ for o in out) == ["Busy", "dict"], out
    wait_state(a, 1, tid, {"confirmed"}, timeout=30)
    assert _log(aegra, tid).count("commit") == 1
    a.close()
    b.close()


@pytest.mark.req("D-3")
def test_aegra_restart_keeps_waiting_and_resumes(aegra, db_url):
    r = _runner(aegra, db_url)
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"}, timeout=30)
    r.close()
    aegra.stop()  # 서버를 내렸다가 같은 DB 로 다시 띄운다 (컨테이너 재시작과 같은 상황)
    aegra.start()
    r2 = _runner(aegra, db_url)
    st = r2.status(1, tid)
    assert st["state"] == "waiting_review" and st["waiting"]["draft_id"] == 1, st
    r2.resume(1, tid, APPROVE)
    wait_state(r2, 1, tid, {"confirmed"}, timeout=30)
    r2.close()


@pytest.mark.req("M-1")
def test_aegra_graph_error_shows_error(aegra, db_url):
    r = _runner(aegra, db_url, assistant="shift_fail")
    tid = new_thread()
    r.start(1, tid)
    st = wait_state(r, 1, tid, {"error"}, timeout=30)
    assert "일부러 실패 (prepare)" in st["error"], st
    with pytest.raises(NotWaiting):
        r.resume(1, tid, APPROVE)
    r.close()


@pytest.mark.req("A-15")
def test_aegra_lock_held_by_other_process_is_busy(aegra, db_url):
    from app.runner import ThreadLock

    r = _runner(aegra, db_url)
    tid = new_thread()
    held = ThreadLock.try_acquire(db_url, tid)
    try:
        with pytest.raises(Busy):
            r.start(1, tid)
    finally:
        held.release()
    r.close()
