"""LocalRunner 시험 (작은 가짜 그래프 + 시험 DB 의 PostgresSaver). 진짜 편성 그래프 · LLM 은 쓰지 않는다."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
import pytest

from app import runner as rt
from app.runner import AlreadyStarted, Busy, LocalRunner, NotWaiting, StaleDraft, ThreadLock
from tests.runtime import dummy_graph
from tests.runtime.conftest import new_thread, wait_state

ROOT = Path(__file__).resolve().parents[2]
APPROVE = {"decision": "approved", "manager_id": 1, "draft_id": 1, "waived_keys": []}


def _child(db_url: str, tid: str, mode: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "DATABASE_URL": db_url}
    return subprocess.Popen([sys.executable, "-m", "tests.runtime._child_runner", db_url, tid, mode],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _concurrent(calls) -> list:
    """calls 를 동시에 출발시킨다. 결과 또는 예외를 순서대로 돌려준다."""
    barrier = threading.Barrier(len(calls))
    out: list = [None] * len(calls)

    def go(i, f):
        barrier.wait()
        try:
            out[i] = f()
        except Exception as e:  # Busy 등도 결과로 모은다
            out[i] = e

    ts = [threading.Thread(target=go, args=(i, f)) for i, f in enumerate(calls)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    return out


# ---------------------------------------------------------------- 기본 흐름

@pytest.mark.req("F-11")
def test_start_runs_in_background_then_waits_for_review(make_local):
    r = make_local(prepare_delay=0.5)
    tid = new_thread()
    t0 = time.monotonic()
    st = r.start(1, tid)
    assert time.monotonic() - t0 < 0.5, "start 는 그래프를 기다리지 않고 바로 돌아와야 한다"
    assert st == {"period_id": 1, "thread_id": tid, "state": "running", "waiting": None, "error": None}
    assert r.status(1, tid)["state"] == "running"
    st = wait_state(r, 1, tid, {"waiting_review"})
    assert st["waiting"]["type"] == "review" and st["waiting"]["draft_id"] == 1
    assert st["error"] is None


@pytest.mark.req("F-11")
def test_resume_with_command_reaches_confirmed(make_local):
    r = make_local()
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    assert r.resume(1, tid, APPROVE)["state"] == "running"
    st = wait_state(r, 1, tid, {"confirmed"})
    assert st["waiting"] is None
    assert dummy_graph.commits(tid) == 1


@pytest.mark.req("F-10")
def test_status_not_started_for_missing_or_unknown_thread(make_local):
    r = make_local()
    assert r.status(5, None) == {"period_id": 5, "thread_id": None, "state": "not_started", "waiting": None,
                                 "error": None}
    assert r.status(5, new_thread())["state"] == "not_started"


@pytest.mark.req("F-10")
def test_start_twice_on_same_thread_is_refused(make_local):
    """기간 1개 = thread 1개: 같은 thread 로 두 번째 편성을 시작하지 않는다 (대기 중이어도)."""
    r = make_local()
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    with pytest.raises(AlreadyStarted):
        r.start(1, tid)
    assert r.status(1, tid)["state"] == "waiting_review"  # 대기는 그대로


@pytest.mark.req("M-6")
def test_contact_wait_maps_to_waiting_contact_and_checks_contact_id(make_local):
    r = make_local(wait="contact")
    tid = new_thread()
    r.start(1, tid)
    st = wait_state(r, 1, tid, {"waiting_contact"})
    assert st["waiting"] == dummy_graph.CONTACT_WAIT
    with pytest.raises(NotWaiting):  # 승인 답은 직원 답장 대기에 넣지 않는다
        r.resume(1, tid, APPROVE)
    with pytest.raises(NotWaiting):  # 다른 연락의 답
        r.resume(1, tid, {"contact_id": 999, "answer": "yes"})
    r.resume(1, tid, {"contact_id": 7, "answer": "yes"})
    wait_state(r, 1, tid, {"confirmed"})


# ---------------------------------------------------------------- 대기가 아닐 때 · 옛 안

@pytest.mark.req("F-11")
def test_resume_when_not_waiting_raises(make_local):
    r = make_local()
    with pytest.raises(NotWaiting):  # 시작 전
        r.resume(1, new_thread(), APPROVE)
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    with pytest.raises(NotWaiting):  # 직원 답장은 승인 대기에 넣지 않는다
        r.resume(1, tid, {"contact_id": 7, "answer": "yes"})


@pytest.mark.req("A-16")
def test_second_approval_after_confirm_is_not_waiting(make_local):
    r = make_local()
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    r.resume(1, tid, APPROVE)
    wait_state(r, 1, tid, {"confirmed"})
    with pytest.raises(NotWaiting):
        r.resume(1, tid, APPROVE)
    assert dummy_graph.commits(tid) == 1


@pytest.mark.req("A-16")
def test_approval_of_old_draft_after_redraft_is_stale(make_local):
    r = make_local()
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    r.resume(1, tid, {"decision": "redraft_requested", "manager_id": 1, "draft_id": 1, "instruction": "다시"})
    deadline = time.monotonic() + 10
    while True:  # 새 안(draft 2) 대기가 될 때까지
        st = r.status(1, tid)
        if st["state"] == "waiting_review" and st["waiting"]["draft_id"] == 2:
            break
        assert time.monotonic() < deadline, st
        time.sleep(0.05)
    with pytest.raises(StaleDraft):
        r.resume(1, tid, APPROVE)  # draft 1 = 옛 안
    assert r.status(1, tid)["waiting"]["draft_id"] == 2  # 옛 안 승인은 반영되지 않았다
    r.resume(1, tid, {**APPROVE, "draft_id": 2})
    wait_state(r, 1, tid, {"confirmed"})
    assert dummy_graph.commits(tid) == 1


# ---------------------------------------------------------------- 잠금 (A-15, V9_ko 509)

@pytest.mark.req("A-15")
def test_concurrent_resume_same_runner_only_one_proceeds(make_local):
    r = make_local(review_delay=0.5)
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    out = _concurrent([lambda: r.resume(1, tid, APPROVE), lambda: r.resume(1, tid, APPROVE)])
    assert sorted(type(o).__name__ for o in out) == ["Busy", "dict"], out
    busy = next(o for o in out if isinstance(o, Busy))
    assert str(busy) == "지금 편성을 처리하고 있습니다"
    assert r.status(1, tid)["state"] == "running"
    wait_state(r, 1, tid, {"confirmed"})
    assert dummy_graph.commits(tid) == 1


@pytest.mark.req("A-15")
def test_concurrent_resume_two_runner_instances_db_lock(make_local):
    """API 프로세스가 둘이어도(러너 인스턴스 둘 = 같은 프로세스 안 기록을 나눠 쓰지 않음) DB 잠금이 하나만 통과시킨다."""
    a, b = make_local(review_delay=0.5), make_local(review_delay=0.5)
    tid = new_thread()
    a.start(1, tid)
    wait_state(a, 1, tid, {"waiting_review"})
    out = _concurrent([lambda: a.resume(1, tid, APPROVE), lambda: b.resume(1, tid, APPROVE)])
    assert sorted(type(o).__name__ for o in out) == ["Busy", "dict"], out
    loser = b if isinstance(out[1], Busy) else a
    assert loser.status(1, tid)["state"] == "running"  # 다른 인스턴스가 쥔 잠금을 pg_locks 로 본다
    wait_state(a, 1, tid, {"confirmed"})
    assert dummy_graph.commits(tid) == 1


@pytest.mark.req("A-15")
def test_without_lock_double_resume_commits_twice(make_local, monkeypatch):
    """깨뜨리는 쪽: 잠금을 없애면 같은 대기에 두 답이 둘 다 들어가 확정 동작이 2번 돈다 → 위 시험이 잡는 것이 이것."""
    a, b = make_local(review_delay=0.5), make_local(review_delay=0.5)
    tid = new_thread()
    a.start(1, tid)
    wait_state(a, 1, tid, {"waiting_review"})

    class NoLock:
        def release(self):
            pass

    monkeypatch.setattr(ThreadLock, "try_acquire", classmethod(lambda cls, url, t: NoLock()))
    out = _concurrent([lambda: a.resume(1, tid, APPROVE), lambda: b.resume(1, tid, APPROVE)])
    assert [type(o).__name__ for o in out] == ["dict", "dict"], out
    a.close(timeout=10)
    b.close(timeout=10)
    assert dummy_graph.commits(tid) == 2


@pytest.mark.req("F-10")
def test_lock_is_per_thread_other_period_not_blocked(make_local):
    r = make_local(review_delay=0.5)
    t1, t2 = new_thread(), new_thread()
    r.start(1, t1)
    r.start(2, t2)
    wait_state(r, 1, t1, {"waiting_review"})
    wait_state(r, 2, t2, {"waiting_review"})
    out = _concurrent([lambda: r.resume(1, t1, APPROVE), lambda: r.resume(2, t2, APPROVE)])
    assert [o["state"] for o in out] == ["running", "running"], out
    wait_state(r, 1, t1, {"confirmed"})
    wait_state(r, 2, t2, {"confirmed"})


@pytest.mark.req("A-15")
def test_lock_held_by_other_connection_makes_start_and_resume_busy(make_local, db_url):
    r = make_local()
    tid = new_thread()
    held = ThreadLock.try_acquire(db_url, tid)
    assert held is not None
    try:
        with pytest.raises(Busy):
            r.start(1, tid)
        assert r.status(1, tid)["state"] == "running"
    finally:
        held.release()
    r.start(1, tid)  # 풀리면 바로 된다
    wait_state(r, 1, tid, {"waiting_review"})


# ---------------------------------------------------------------- 재시작 · 강제 종료 (D-3, F-11)

@pytest.mark.req("D-3")
def test_restart_new_runner_instance_sees_wait_and_resumes(make_local):
    a = make_local()
    tid = new_thread()
    a.start(1, tid)
    wait_state(a, 1, tid, {"waiting_review"})
    a.close()  # 러너(=API 프로세스)를 내린다
    b = make_local()  # 새 saver · 새 pool · 새 그래프
    st = b.status(1, tid)
    assert st["state"] == "waiting_review" and st["waiting"]["draft_id"] == 1
    b.resume(1, tid, APPROVE)
    wait_state(b, 1, tid, {"confirmed"})


@pytest.mark.req("D-3")
def test_restart_real_process_exit_then_resume(make_local, db_url):
    """다른 프로세스가 승인 대기까지 돌리고 정리 없이 끝남(D-2 미리 돌리기) → 이 프로세스에서 이어서 확정."""
    tid = new_thread()
    p = _child(db_url, tid, "to_waiting")
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err[-2000:]
    assert json.loads(out.strip().splitlines()[-1])["state"] == "waiting_review"
    r = make_local()
    assert r.status(1, tid)["state"] == "waiting_review"
    r.resume(1, tid, APPROVE)
    wait_state(r, 1, tid, {"confirmed"})


@pytest.mark.req("A-15")
def test_holder_killed_releases_lock_and_thread_shows_stalled(make_local, db_url):
    """잠금을 쥔 프로세스를 kill -9 → 잠금이 풀린다(다음 요청이 영원히 Busy 가 되지 않는다).
    실행 도중 죽은 run 은 자동으로 이어 돌리지 않고 error(「중간에 멈춤」)로 보인다."""
    tid = new_thread()
    p = _child(db_url, tid, "slow")
    try:
        assert p.stdout.readline().strip() == "STARTED", p.stderr.read()[-2000:] if p.poll() is not None else ""
        r = make_local()
        assert r.status(1, tid)["state"] == "running"  # 다른 프로세스가 쥔 잠금
        with pytest.raises(Busy):
            r.resume(1, tid, APPROVE)
        os.kill(p.pid, signal.SIGKILL)
        p.wait(10)
        killed = time.monotonic()
        st = wait_state(r, 1, tid, {"error"}, timeout=10)
        assert time.monotonic() - killed < 5
        assert "중간에 멈췄습니다" in st["error"]
        with pytest.raises(NotWaiting):
            r.resume(1, tid, APPROVE)
    finally:
        if p.poll() is None:
            p.kill()


# ---------------------------------------------------------------- 오류

@pytest.mark.req("M-1")
def test_error_in_graph_shows_error_state(make_local):
    r = make_local(fail_in="prepare")
    tid = new_thread()
    r.start(1, tid)
    st = wait_state(r, 1, tid, {"error"})
    assert "일부러 실패 (prepare)" in st["error"]
    with pytest.raises(NotWaiting):
        r.resume(1, tid, APPROVE)


@pytest.mark.req("M-1")
def test_error_after_resume_shows_error_state(make_local):
    r = make_local(fail_in="review")
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    r.resume(1, tid, APPROVE)
    st = wait_state(r, 1, tid, {"error"})
    assert "일부러 실패 (review 뒤)" in st["error"]
    assert dummy_graph.commits(tid) == 0
    with pytest.raises(NotWaiting):  # 오류 난 대기에 같은 답을 또 넣지 않는다
        r.resume(1, tid, APPROVE)
    r.close()
    assert make_local(fail_in="review").status(1, tid)["state"] == "error"  # 재시작 뒤에도 (체크포인트의 작업 오류)


@pytest.mark.req("D-3")
def test_error_survives_restart(make_local, db_url):
    """오류는 메모리만이 아니라 체크포인트(작업 오류)에도 남아, 다른 프로세스에서도 error 로 보인다."""
    tid = new_thread()
    p = _child(db_url, tid, "fail")
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err[-2000:]
    r = make_local(fail_in="prepare")
    st = r.status(1, tid)
    assert st["state"] == "error" and "일부러 실패 (prepare)" in st["error"], st


# ---------------------------------------------------------------- 체크포인트 위치 · 입구

@pytest.mark.req("F-11")
def test_checkpoints_live_in_own_schema_not_public(make_local, db_url):
    r = make_local()
    tid = new_thread()
    r.start(1, tid)
    wait_state(r, 1, tid, {"waiting_review"})
    with psycopg.connect(db_url) as c:
        lg = c.execute("SELECT count(*) FROM lg_checkpoint.checkpoints WHERE thread_id = %s", (tid,)).fetchone()[0]
        pub = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' "
                        "AND table_name LIKE 'checkpoint%%'").fetchone()[0]
    assert lg > 0 and pub == 0


@pytest.mark.req("D-1")
def test_get_runner_follows_runner_setting(monkeypatch, db_url):
    rt.reset_runner()
    monkeypatch.setenv("RUNNER", "aegra")
    monkeypatch.setenv("AEGRA_URL", "http://127.0.0.1:9")  # 만들 때는 접속하지 않는다
    try:
        r = rt.get_runner()
        assert isinstance(r, rt.AegraRunner) and rt.get_runner() is r
        rt.reset_runner()
        monkeypatch.setenv("RUNNER", "nope")
        with pytest.raises(ValueError):
            rt.get_runner()
    finally:
        rt.reset_runner()


@pytest.mark.req("D-1")
def test_local_runner_without_graph_factory_uses_app_graph(db_url, monkeypatch):
    """기본값은 app.graph.build.build_graph. 아직 없으면 무슨 일인지 말하는 오류로 멈춘다."""
    try:
        import app.graph.build  # noqa: F401
    except ImportError:
        with pytest.raises(RuntimeError, match="app.graph.build.build_graph"):
            LocalRunner(db_url)
        return
    r = LocalRunner(db_url)
    r.close()
