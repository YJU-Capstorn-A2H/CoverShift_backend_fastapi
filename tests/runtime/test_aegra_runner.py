"""AegraRunner 단위 시험 — 가짜 SDK 클라이언트로 thread state → RunStatus 대응, 잠금 · wait_idle 을 본다.
잠금은 진짜 시험 DB(advisory lock)를 쓴다. 진짜 Aegra 서버 시험은 test_aegra_e2e.py."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import psycopg
import pytest

from app.runner import AegraRunner, AlreadyStarted, Busy, NotWaiting, StaleDraft, ThreadLock, lock_held
from tests.runtime.conftest import new_thread
from tests.runtime.dummy_graph import CONTACT_WAIT, review_wait

APPROVE = {"decision": "approved", "manager_id": 1, "draft_id": 1, "waived_keys": []}


class NotFound(Exception):
    def __init__(self):
        super().__init__("404")
        self.response = SimpleNamespace(status_code=404)


class FakeAegra:
    """langgraph_sdk SyncLangGraphClient 에서 러너가 쓰는 부분만."""

    def __init__(self):
        self.threads_db: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.runs_db: dict[str, list[dict]] = {}  # 최신이 앞
        self.created: list[dict] = []
        self.mu = threading.Lock()
        self.threads = SimpleNamespace(create=self._t_create, get=self._t_get, get_state=self._t_state)
        self.runs = SimpleNamespace(list=self._r_list, create=self._r_create, get=self._r_get)

    # 시험이 상태를 꾸민다
    def put(self, tid, state, runs=(), thread_status="idle"):
        self.threads_db[tid] = {"thread_id": tid, "status": thread_status}
        self.states[tid] = state
        self.runs_db[tid] = list(runs)

    def finish(self, tid, status="success"):
        with self.mu:
            for r in self.runs_db.get(tid, []):
                if r["status"] in ("pending", "running"):
                    r["status"] = status

    # SDK 흉내
    def _t_create(self, *, thread_id, if_exists=None, metadata=None, **kw):
        if thread_id not in self.threads_db:
            self.put(thread_id, {"values": {}, "next": [], "checkpoint": {"checkpoint_id": None}, "tasks": [],
                                 "interrupts": []})
        return self.threads_db[thread_id]

    def _t_get(self, tid, **kw):
        if tid not in self.threads_db:
            raise NotFound()
        return self.threads_db[tid]

    def _t_state(self, tid, **kw):
        if tid not in self.states:
            raise NotFound()
        return self.states[tid]

    def _r_list(self, tid, *, limit=10, **kw):
        if tid not in self.threads_db:
            raise NotFound()
        with self.mu:
            return [dict(r) for r in self.runs_db.get(tid, [])[:limit]]

    def _r_create(self, tid, assistant_id, **kw):
        with self.mu:
            run = {"run_id": f"run-{len(self.created) + 1}", "thread_id": tid, "status": "running"}
            self.created.append({"thread_id": tid, "assistant_id": assistant_id, **kw})
            self.runs_db.setdefault(tid, []).insert(0, run)
            return dict(run)

    def _r_get(self, tid, run_id, **kw):
        with self.mu:
            return dict(next(r for r in self.runs_db[tid] if r["run_id"] == run_id))


def waiting_state(value, *, iid="i-1", top_level=True):
    intr = [{"id": iid, "value": value}]
    return {"values": {"draft_id": value.get("draft_id")}, "next": ["review"], "checkpoint": {"checkpoint_id": "c1"},
            "tasks": [{"id": "t1", "name": "review", "error": None, "interrupts": [] if top_level else intr}],
            "interrupts": intr if top_level else []}


@pytest.fixture
def fake():
    return FakeAegra()


@pytest.fixture
def make_aegra(db_url, fake):
    made = []

    def make(**kw):
        r = AegraRunner(database_url=db_url, client=fake, assistant_id="shift",
                        idle_wait_sec=kw.pop("idle_wait_sec", 0.3), poll_interval=0.02, **kw)
        made.append(r)
        return r

    yield make
    for tid in list(fake.runs_db):
        fake.finish(tid)
    for r in made:
        r.close(5)


def _held(db_url, tid) -> bool:
    with psycopg.connect(db_url, autocommit=True) as c:
        return lock_held(c, tid)


# ---------------------------------------------------------------- 상태 대응 (RunStatus)

CHECKPOINT = {"checkpoint_id": "c9"}
CASES = {
    "fresh_thread": ({"values": {}, "next": [], "checkpoint": {"checkpoint_id": None}, "tasks": []}, [], "not_started"),
    "running": ({"values": {}, "next": ["prepare"], "checkpoint": CHECKPOINT, "tasks": []},
                [{"run_id": "r1", "status": "running"}], "running"),
    "pending": ({"values": {}, "next": [], "checkpoint": {"checkpoint_id": None}, "tasks": []},
                [{"run_id": "r1", "status": "pending"}], "running"),
    "review_top_level": (waiting_state(review_wait(4)), [{"run_id": "r1", "status": "interrupted"}], "waiting_review"),
    "contact_in_tasks": (waiting_state(CONTACT_WAIT, top_level=False), [{"run_id": "r1", "status": "success"}],
                         "waiting_contact"),
    "finished": ({"values": {"log": ["commit"]}, "next": [], "checkpoint": CHECKPOINT, "tasks": []},
                 [{"run_id": "r2", "status": "success"}], "confirmed"),
    "run_error": ({"values": {}, "next": ["prepare"], "checkpoint": CHECKPOINT, "tasks": []},
                  [{"run_id": "r1", "status": "error", "error_message": "RuntimeError: LLM 시간 초과"}], "error"),
    "task_error_hides_wait": ({**waiting_state(review_wait(1)), "tasks": [
        {"id": "t1", "name": "review", "error": "RuntimeError: boom", "interrupts": []}]},
        [{"run_id": "r1", "status": "success"}], "error"),
    "stalled": ({"values": {}, "next": ["prepare"], "checkpoint": CHECKPOINT, "tasks": []},
                [{"run_id": "r1", "status": "success"}], "error"),
    "unknown_wait_type": (waiting_state({"type": "weird"}), [], "error"),
}


@pytest.mark.req("F-11")
@pytest.mark.parametrize("case", list(CASES))
def test_thread_state_maps_to_run_status(make_aegra, fake, case):
    state, runs, want = CASES[case]
    tid = new_thread()
    fake.put(tid, state, runs)
    st = make_aegra().status(9, tid)
    assert st["state"] == want, st
    assert st["period_id"] == 9 and st["thread_id"] == tid
    if want.startswith("waiting_"):
        assert st["waiting"]["type"] in ("review", "contact") and st["error"] is None
    if case == "run_error":
        assert st["error"] == "RuntimeError: LLM 시간 초과"
    if case == "stalled":
        assert "중간에 멈췄습니다" in st["error"]


@pytest.mark.req("F-10")
def test_status_unknown_thread_and_none(make_aegra):
    r = make_aegra()
    assert r.status(1, None)["state"] == "not_started"
    assert r.status(1, new_thread())["state"] == "not_started"  # Aegra 404


# ---------------------------------------------------------------- start · resume

@pytest.mark.req("F-10")
def test_start_creates_thread_and_run_with_period_input(make_aegra, fake, db_url):
    r = make_aegra()
    tid = new_thread()
    assert r.start(3, tid)["state"] == "running"
    assert fake.created == [{"thread_id": tid, "assistant_id": "shift", "input": {"period_id": 3},
                             "multitask_strategy": "reject"}]
    assert r.status(3, tid)["state"] == "running"
    assert _held(db_url, tid), "run 이 도는 동안 잠금을 쥔다"
    fake.finish(tid, "interrupted")
    deadline = time.monotonic() + 5
    while _held(db_url, tid):
        assert time.monotonic() < deadline, "run 이 끝났는데 잠금을 놓지 않음"
        time.sleep(0.02)


@pytest.mark.req("F-10")
def test_start_on_started_thread_is_refused(make_aegra, fake):
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(1)), [{"run_id": "r1", "status": "interrupted"}])
    with pytest.raises(AlreadyStarted):
        make_aegra().start(1, tid)
    assert fake.created == []


@pytest.mark.req("F-11")
def test_resume_sends_command_for_that_interrupt(make_aegra, fake):
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(1), iid="intr-xyz"), [{"run_id": "r1", "status": "interrupted"}])
    st = make_aegra().resume(1, tid, APPROVE)
    assert st["state"] == "running"
    assert fake.created[-1]["command"] == {"resume": {"intr-xyz": APPROVE}}
    assert fake.created[-1]["multitask_strategy"] == "reject"


@pytest.mark.req("F-11")
def test_resume_without_wait_is_not_waiting(make_aegra, fake):
    r = make_aegra()
    with pytest.raises(NotWaiting):
        r.resume(1, new_thread(), APPROVE)  # thread 없음
    tid = new_thread()
    fake.put(tid, CASES["finished"][0], CASES["finished"][1])
    with pytest.raises(NotWaiting):
        r.resume(1, tid, APPROVE)  # 이미 확정
    assert fake.created == []


@pytest.mark.req("A-16")
def test_resume_old_draft_is_stale_and_contact_mismatch_not_waiting(make_aegra, fake):
    r = make_aegra()
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(2)), [])
    with pytest.raises(StaleDraft):
        r.resume(1, tid, APPROVE)  # draft 1 에 대한 승인, 대기는 draft 2
    with pytest.raises(NotWaiting):
        r.resume(1, tid, {"contact_id": 7, "answer": "yes"})
    assert fake.created == []


# ---------------------------------------------------------------- 잠금 · wait_idle (A-15)

@pytest.mark.req("A-15")
def test_resume_busy_when_lock_held_elsewhere(make_aegra, fake, db_url):
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(1)), [])
    held = ThreadLock.try_acquire(db_url, tid)
    try:
        with pytest.raises(Busy, match="지금 편성을 처리하고 있습니다"):
            make_aegra().resume(1, tid, APPROVE)
    finally:
        held.release()
    assert fake.created == []


@pytest.mark.req("A-15")
def test_concurrent_resume_only_one_run_created(make_aegra, fake):
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(1)), [])
    a, b = make_aegra(), make_aegra()
    barrier = threading.Barrier(2)
    out = []

    def go(r):
        barrier.wait()
        try:
            out.append(r.resume(1, tid, APPROVE))
        except Exception as e:
            out.append(e)

    ts = [threading.Thread(target=go, args=(x,)) for x in (a, b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert sorted(type(o).__name__ for o in out) == ["Busy", "dict"], out
    assert len(fake.created) == 1


@pytest.mark.req("A-15")
def test_wait_idle_busy_when_server_run_outlives_dead_holder(make_aegra, fake, db_url):
    """prototype_aegra test_lock_crash: 잠금을 쥔 입구가 죽어 잠금은 풀렸는데 서버 run 은 계속 돈다 → 잠금만 보면 통과해 버린다."""
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(1)), [{"run_id": "orphan", "status": "running"}])
    assert not _held(db_url, tid)  # 잠금은 아무도 안 쥠
    t0 = time.monotonic()
    with pytest.raises(Busy):
        make_aegra(idle_wait_sec=0.3).resume(1, tid, APPROVE)
    assert time.monotonic() - t0 >= 0.3
    assert fake.created == []


@pytest.mark.req("A-15")
def test_wait_idle_proceeds_when_server_run_finishes_in_time(make_aegra, fake):
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(1)), [{"run_id": "orphan", "status": "running"}])
    threading.Timer(0.1, fake.finish, args=(tid, "interrupted")).start()
    assert make_aegra(idle_wait_sec=2.0).resume(1, tid, APPROVE)["state"] == "running"
    assert len(fake.created) == 1


@pytest.mark.req("A-15")
def test_without_wait_idle_resume_would_go_through(make_aegra, fake, monkeypatch):
    """깨뜨리는 쪽: wait_idle 을 빼면 서버에 도는 run 이 있어도 resume 이 만들어진다 → 위 시험이 잡는 것이 이것."""
    tid = new_thread()
    fake.put(tid, waiting_state(review_wait(1)), [{"run_id": "orphan", "status": "running"}])
    r = make_aegra()
    monkeypatch.setattr(r, "_wait_idle", lambda thread_id: None)
    r.resume(1, tid, APPROVE)
    assert len(fake.created) == 1
