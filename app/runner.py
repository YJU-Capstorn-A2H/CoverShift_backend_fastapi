"""러너 — 입구 API 와 편성 그래프 사이 (runtime 에이전트 소유). 계약: API_CONTRACT.md 「러너 계약」.

- 편성 기간 1개 = thread 1개 (V9_ko 98, 275 · F-10). thread_id 는 shift_periods.thread_id 에 API 가 적는다.
- 대기 중에는 돌지 않고 체크포인트(Postgres)에 상태만 남는다. 재개는 같은 thread 에 Command(resume=…) (V9_ko 89 · F-11).
- 같은 thread 에 요청이 겹치면 하나만 처리하고 나머지는 Busy → 409 「いま編成を処理しています」 (V9_ko 509 · A-15).
  잠금 = Postgres advisory lock `pg_try_advisory_lock(hashtextextended(thread_id, 0))` 를 전용 연결에서 run 이 끝날 때까지 쥔다.
  근거: prototype_aegra/README.md 「대안 A」 — 잠금 없음 5/5 깨짐 / 잠금 있음 5/5 정상.
  잠금을 쥔 프로세스가 죽으면 0.01초 만에 풀리지만 Aegra 서버의 run 은 계속 돈다 → AegraRunner 는 잠금을 잡은 뒤
  그 thread 에 도는 run 이 없는지(wait_idle) 한 번 더 본다 (prototype_aegra test_lock_crash.py, result_lock_crash.txt).

RUNNER=local → LocalRunner (같은 프로세스, PostgresSaver) / RUNNER=aegra → AegraRunner (Aegra HTTP, langgraph_sdk).
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from app.config import get_settings

log = logging.getLogger("runner")

BUSY_MESSAGE = "いま編成を処理しています"  # V9_ko 509

STATES = ("not_started", "running", "waiting_review", "waiting_contact", "confirmed", "error")
WAIT_STATE = {"review": "waiting_review", "contact": "waiting_contact"}
ACTIVE_RUN_STATUSES = ("pending", "running")


# ---------------------------------------------------------------- 예외 (API 가 409 로 바꾼다)

class Busy(Exception):
    """같은 thread 에 도는 run 이 있음 → API 가 409 BUSY (A-15)."""

    def __init__(self, message: str = BUSY_MESSAGE):
        super().__init__(message)


class NotWaiting(Exception):
    """resume 할 interrupt 가 없음 (또는 지금 대기와 맞지 않는 답) → 409 NOT_WAITING."""


class StaleDraft(NotWaiting):
    """(가정 신규) 승인 대기 중인 안(draft_id)과 다른 안에 대한 답 → 409 STALE_DRAFT.
    NotWaiting 의 하위 클래스라 API 가 NotWaiting 만 잡아도 409 가 된다 (확정되지 않는 쪽으로 안전).
    잠금 안에서 비교해야 「API 가 본 뒤 ~ resume 사이」에 안이 바뀌는 경우도 막힌다."""


class AlreadyStarted(Exception):
    """(가정 신규) 이미 체크포인트가 있는 thread 에 start → API 가 409 ALREADY_STARTED. 같은 thread 로 두 번 돌지 않게 한다."""


# ---------------------------------------------------------------- 계약 형

class GraphRunner(Protocol):
    def start(self, period_id: int, thread_id: str) -> dict: ...

    def status(self, period_id: int, thread_id: str | None) -> dict: ...

    def resume(self, period_id: int, thread_id: str, payload: dict) -> dict: ...


GraphFactory = Callable[..., Any]  # build_graph(checkpointer=None, deps=None) -> CompiledStateGraph


def run_status(period_id: int, thread_id: str | None, state: str, waiting: dict | None = None,
               error: str | None = None) -> dict:
    """RunStatus (API_CONTRACT.md) 한 개."""
    assert state in STATES, state
    return {"period_id": period_id, "thread_id": thread_id, "state": state, "waiting": waiting, "error": error}


def start_input(period_id: int) -> dict:
    """(가정 신규) 그래프 시작 입력. 러너 계약이 period_id 만 받으므로 {"period_id"} 만 넘기고 나머지는 그래프가 DB 에서 읽는다."""
    return {"period_id": period_id}


def status_from_parts(period_id: int, thread_id: str, *, has_checkpoint: bool, interrupts: list[tuple[str | None, Any]],
                      task_errors: list[str], next_nodes: list[str] | tuple[str, ...],
                      run_error: str | None = None) -> dict:
    """체크포인트 조각 → RunStatus. Local · Aegra 가 같은 규칙을 쓴다 (도는 중인지는 부르는 쪽이 먼저 본다).

    순서: 노드 오류 → run 오류 → 대기(interrupt) → 체크포인트 없음 → 끝남(confirmed) → 그 밖(멈춤 = error).
    오류가 대기보다 먼저다: resume 받은 노드가 예외로 끝나면 LangGraph 는 그 대기(interrupt)를 아직 열린 것으로 보인다
    (시험 test_error_after_resume_shows_error_state 에서 확인). 이때 waiting 으로 보이면 점장이 같은 승인을 또 누르게 된다.
    """
    if task_errors:
        return run_status(period_id, thread_id, "error", error=task_errors[0])
    if run_error:
        return run_status(period_id, thread_id, "error", error=run_error)
    if interrupts:
        value = interrupts[0][1]
        kind = value.get("type") if isinstance(value, dict) else None
        if kind not in WAIT_STATE:
            return run_status(period_id, thread_id, "error", error=f"不明な待ち type: {kind!r}")
        return run_status(period_id, thread_id, WAIT_STATE[kind], waiting=value)
    if not has_checkpoint:
        return run_status(period_id, thread_id, "not_started")
    if not next_nodes:
        return run_status(period_id, thread_id, "confirmed")  # 계약: 그래프가 끝남(다음 노드 없음) = 확정
    # 다음 노드가 남았는데 도는 run 도 대기도 없다 = 실행 도중 프로세스가 죽은 경우 (자동 이어 돌리기는 하지 않는다)
    return run_status(period_id, thread_id, "error",
                      error=f"編成が途中で止まりました(次のノード {list(next_nodes)})。運営の確認が必要です")


def match_payload(waiting: dict, payload: dict) -> None:
    """(가정 신규) 지금 대기와 답의 종류가 맞는지 잠금 안에서 확인. 맞지 않으면 NotWaiting / StaleDraft.

    resume payload (API_CONTRACT.md): review → {"decision", "manager_id", "draft_id", ...} / contact → {"contact_id", "answer"}.
    늦게 온 직원 답장이 승인 노드로 들어가거나, 옛 안에 대한 승인이 새 안에 반영되는 것을 막는다 (prototype_aegra E4 「지금 멈춘 대기가 아니면 버린다」).
    """
    kind = waiting.get("type")
    if kind == "review":
        if "decision" not in payload:
            raise NotWaiting("承認待ちです。従業員の返事は、いまは受け付けられません")
        if payload.get("draft_id") != waiting.get("draft_id"):
            raise StaleDraft(f"見ている案(draft {payload.get('draft_id')})が最新の案(draft {waiting.get('draft_id')})ではありません")
    elif kind == "contact":
        if "contact_id" not in payload:
            raise NotWaiting("従業員の返事を待っています。承認は、いまは受け付けられません")
        if payload.get("contact_id") != waiting.get("contact_id"):
            raise NotWaiting(f"いま待っている連絡(contact {waiting.get('contact_id')})ではありません")
    else:
        raise NotWaiting(f"不明な待ち type: {kind!r}")


# ---------------------------------------------------------------- thread 잠금 (A-15, V9_ko 509)

LOCK_SQL = "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS got"
# 다른 연결이 그 키를 쥐고 있는지 pg_locks 로 본다. 잠금을 「시도」하지 않으므로 status 를 자주 불러도 resume 을 방해하지 않는다.
# bigint 키는 pg_locks 에 classid = 위 32비트, objid = 아래 32비트, objsubid = 1 로 보인다 (시험 DB 에서 확인함).
HELD_SQL = """
SELECT EXISTS (
  SELECT 1 FROM pg_locks
   WHERE locktype = 'advisory' AND granted AND objsubid = 1
     AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
     AND classid = ((k >> 32) & 4294967295)::oid AND objid = (k & 4294967295)::oid
) AS held
FROM (SELECT hashtextextended(%s, 0) AS k) s
"""


class ThreadLock:
    """한 thread 의 advisory lock 을 쥔 전용 연결. 연결이 닫히면(프로세스가 죽어도) 잠금이 풀린다.

    풀(pool) 연결을 쓰지 않는다: 세션 잠금을 쥔 연결이 풀로 돌아가면 잠금이 새어 남는다.
    """

    def __init__(self, conn: psycopg.Connection, thread_id: str):
        self._conn = conn
        self.thread_id = thread_id
        self._released = False

    @classmethod
    def try_acquire(cls, url: str, thread_id: str) -> ThreadLock | None:
        conn = psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5)
        try:
            got = conn.execute(LOCK_SQL, (thread_id,)).fetchone()["got"]
        except BaseException:
            conn.close()
            raise
        if not got:
            conn.close()
            return None
        return cls(conn, thread_id)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (self.thread_id,))
        except Exception:  # 연결이 이미 끊겼으면 잠금도 이미 풀렸다
            log.warning("잠금 해제 중 연결 오류 (thread=%s) — 연결 종료로 풀린다", self.thread_id)
        finally:
            self._conn.close()


def lock_held(conn: psycopg.Connection, thread_id: str) -> bool:
    row = conn.execute(HELD_SQL, (thread_id,)).fetchone()
    return bool(row["held"] if isinstance(row, dict) else row[0])


# ---------------------------------------------------------------- 백그라운드 실행 기록 (같은 프로세스 안)

class _Runs:
    """이 프로세스에서 도는 run(스레드)과 마지막 오류. 다른 프로세스의 run 은 DB 잠금으로 본다."""

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._alive: dict[str, threading.Thread] = {}
        self._errors: dict[str, str] = {}

    def is_alive(self, thread_id: str) -> bool:
        with self._mu:
            return thread_id in self._alive

    def error(self, thread_id: str) -> str | None:
        with self._mu:
            return self._errors.get(thread_id)

    def spawn(self, thread_id: str, target: Callable[[], None], lock: ThreadLock) -> None:
        def body() -> None:
            try:
                target()
            except BaseException as e:  # 그래프 안의 예외를 status 의 error 로 보인다
                log.exception("run 실패 (thread=%s)", thread_id)
                with self._mu:
                    self._errors[thread_id] = f"{type(e).__name__}: {e}"
            finally:
                lock.release()  # 잠금을 먼저 푼다. 그 사이 요청은 아직 is_alive → Busy (몇 ms)
                with self._mu:
                    self._alive.pop(thread_id, None)

        t = threading.Thread(target=body, name=f"run-{thread_id}", daemon=True)
        with self._mu:
            if thread_id in self._alive:  # try_acquire 이후라 여기 올 일은 없지만, 두 번 띄우지 않는다
                lock.release()
                raise Busy()
            self._errors.pop(thread_id, None)
            self._alive[thread_id] = t
        t.start()

    def join_all(self, timeout: float) -> None:
        with self._mu:
            threads = list(self._alive.values())
        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))


# ---------------------------------------------------------------- LocalRunner (RUNNER=local)

def default_graph_factory() -> GraphFactory:
    # (추가 · 아즈키 11/19) GRAPH_IMPL=azuki 이면 아즈키의 그래프(app/graph)를, 그 외(기본)는 승혁의 그래프(app/graph_seung)를 쓴다
    import os
    try:
        if os.environ.get("GRAPH_IMPL", "seung") == "azuki":
            from app.graph_azuki_bridge import build_graph
        else:
            from app.graph_seung.build import build_graph  # 그래프 에이전트 소유
    except ImportError as e:
        raise RuntimeError("app.graph_seung.build.build_graph 를 import 하지 못했습니다 (그래프가 아직 없거나 import 오류)") from e
    return build_graph


class LocalRunner:
    """같은 프로세스에서 그래프를 돌린다. 체크포인트 = PostgresSaver (langgraph-checkpoint-postgres 3.2.0).

    (가정 신규) 체크포인트 표는 public 이 아닌 별도 스키마(기본 lg_checkpoint)에 만든다.
    이유: 앱 표(public 18개, tests/test_harness.py 가 개수를 센다)와 섞지 않고, 지울 때도 스키마 하나로 끝나게.
    """

    def __init__(self, database_url: str | None = None, graph_factory: GraphFactory | None = None, *,
                 deps: Any = None, checkpoint_schema: str = "lg_checkpoint", pool_max: int = 10):
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool

        self._url = database_url or get_settings().database_url
        self._schema = checkpoint_schema
        self._ensure_schema()
        # PostgresSaver 가 요구하는 연결 설정: autocommit · prepare_threshold=0 · dict_row
        self._pool = ConnectionPool(
            self._url, min_size=1, max_size=pool_max, open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                    "options": f"-c search_path={checkpoint_schema}"},
        )
        self._pool.open(wait=True, timeout=10)
        self._saver = PostgresSaver(self._pool)
        self._setup_saver()
        factory = graph_factory or default_graph_factory()
        self._graph = factory(checkpointer=self._saver, deps=deps)
        self._runs = _Runs()

    # -- 준비
    def _ensure_schema(self) -> None:
        with psycopg.connect(self._url, autocommit=True) as c:
            c.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self._schema)))

    def _setup_saver(self) -> None:
        # 러너 두 개가 동시에 setup() 하면 CREATE TABLE 이 부딪힐 수 있어 고정 키 잠금 안에서 한 번만
        with psycopg.connect(self._url, autocommit=True) as c:
            c.execute("SELECT pg_advisory_lock(hashtextextended('covershift:checkpoint_setup', 0))")
            try:
                self._saver.setup()
            finally:
                c.execute("SELECT pg_advisory_unlock(hashtextextended('covershift:checkpoint_setup', 0))")

    def close(self, timeout: float = 30.0) -> None:
        self._runs.join_all(timeout)
        self._pool.close()

    # -- 공통
    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _acquire(self, thread_id: str) -> ThreadLock:
        if self._runs.is_alive(thread_id):
            raise Busy()
        lock = ThreadLock.try_acquire(self._url, thread_id)
        if lock is None:
            raise Busy()
        return lock

    def _checkpoint_status(self, period_id: int, thread_id: str) -> tuple[dict, list[tuple[str | None, Any]]]:
        """체크포인트만 보고 정한 RunStatus 와 열린 대기 [(interrupt id, 값)]. 도는 중인지는 부르는 쪽이 먼저 본다."""
        snap = self._graph.get_state(self._config(thread_id))
        intr = list(snap.interrupts) or [i for t in snap.tasks for i in t.interrupts]
        pending = [(getattr(i, "id", None), i.value) for i in intr]
        st = status_from_parts(
            period_id, thread_id, has_checkpoint=snap.created_at is not None, interrupts=pending,
            task_errors=[str(t.error) for t in snap.tasks if getattr(t, "error", None)],
            next_nodes=snap.next, run_error=self._runs.error(thread_id))
        return st, pending

    def _invoke(self, thread_id: str, inp: Any) -> Callable[[], None]:
        # durability="sync": 단계마다 체크포인트를 다 쓴 뒤 다음 단계로 → 대기 상태가 잠금을 풀기 전에 반드시 DB 에 있다 (D-3)
        return lambda: self._graph.invoke(inp, self._config(thread_id), durability="sync")

    # -- 계약
    def start(self, period_id: int, thread_id: str) -> dict:
        lock = self._acquire(thread_id)
        try:
            snap = self._graph.get_state(self._config(thread_id))
            if snap.created_at is not None or snap.next or snap.values:
                raise AlreadyStarted(f"thread {thread_id} はすでに開始されています")
            self._runs.spawn(thread_id, self._invoke(thread_id, start_input(period_id)), lock)
        except BaseException:
            lock.release()
            raise
        return run_status(period_id, thread_id, "running")

    def resume(self, period_id: int, thread_id: str, payload: dict) -> dict:
        from langgraph.types import Command

        lock = self._acquire(thread_id)
        try:
            st, pending = self._checkpoint_status(period_id, thread_id)
            if st["state"] not in WAIT_STATE.values():  # resume 은 status 가 waiting_* 일 때만 (오류 난 대기에 또 넣지 않는다)
                raise NotWaiting(f"再開できる待ちがありません(state={st['state']})")
            iid, value = pending[0]
            match_payload(value, payload)
            # {interrupt id: 값} — 확인한 바로 그 대기에만 답한다 (대기가 하나면 Command(resume=payload) 와 같다)
            cmd = Command(resume={iid: payload}) if iid else Command(resume=payload)
            self._runs.spawn(thread_id, self._invoke(thread_id, cmd), lock)
        except BaseException:
            lock.release()
            raise
        return run_status(period_id, thread_id, "running")

    def status(self, period_id: int, thread_id: str | None) -> dict:
        if not thread_id:
            return run_status(period_id, None, "not_started")
        if self._runs.is_alive(thread_id):
            return run_status(period_id, thread_id, "running")
        with self._pool.connection() as c:
            if lock_held(c, thread_id):  # 다른 프로세스(다른 API 워커 · 시연 스크립트)가 돌리는 중
                return run_status(period_id, thread_id, "running")
        return self._checkpoint_status(period_id, thread_id)[0]


# ---------------------------------------------------------------- AegraRunner (RUNNER=aegra)

def _is_not_found(e: Exception) -> bool:
    resp = getattr(e, "response", None)
    return getattr(resp, "status_code", None) == 404


class AegraRunner:
    """Aegra 0.10.8 서버에 run 을 만든다 (langgraph_sdk 0.4.8 sync client). 체크포인트는 Aegra 가 Postgres 에 저장 (V9_ko 507).

    잠금은 이 프로세스(입구 API)에서 앱 DB 에 잡는다. run 이 끝날 때까지 백그라운드 스레드가 잠금을 쥐고 run 상태를 본다.
    잠금을 잡은 뒤 서버에 도는 run 이 있으면 idle_wait_sec 동안 기다리고, 그래도 돌면 Busy (prototype_aegra wait_idle).
    (가정 신규) idle_wait_sec 기본 3초 — prototype_skeleton 결함 6 「대기에 멈춘 뒤에도 약 1.7초 잠금을 더 쥠」보다 길게.
    multitask_strategy="reject" 도 주지만 Aegra 0.10.8 은 이 값을 막는 데 쓰지 않는다(services/run_preparation.py 에서 저장만 함,
    prototype_aegra 「reject: 거절 오류 없음」) → 막는 것은 잠금이다.
    """

    def __init__(self, aegra_url: str | None = None, database_url: str | None = None, *,
                 assistant_id: str = "shift", client: Any = None, idle_wait_sec: float = 3.0,
                 poll_interval: float = 0.5, max_run_sec: float = 3 * 3600):
        s = get_settings()
        self._url = database_url or s.database_url
        if client is None:
            from langgraph_sdk import get_sync_client
            client = get_sync_client(url=aegra_url or s.aegra_url, api_key=None)
        self._client = client
        self._assistant = assistant_id
        self._idle_wait = idle_wait_sec
        self._poll = poll_interval
        self._max_run = max_run_sec
        self._runs = _Runs()

    def close(self, timeout: float = 5.0) -> None:
        self._runs.join_all(timeout)

    # -- 서버 조회
    def _active_runs(self, thread_id: str) -> list[dict]:
        try:
            runs = self._client.runs.list(thread_id, limit=20)
        except Exception as e:
            if _is_not_found(e):
                return []
            raise
        return [r for r in runs if r.get("status") in ACTIVE_RUN_STATUSES]

    def _wait_idle(self, thread_id: str) -> None:
        """잠금을 잡은 뒤에도 서버에 도는 run 이 있으면(앞 입구가 죽어 잠금만 풀린 경우) 잠깐 기다리고, 안 끝나면 Busy."""
        deadline = time.monotonic() + self._idle_wait
        while self._active_runs(thread_id):
            if time.monotonic() >= deadline:
                raise Busy()
            time.sleep(min(self._poll, self._idle_wait) or 0.05)

    def _get_state(self, thread_id: str) -> dict | None:
        try:
            return self._client.threads.get_state(thread_id)
        except Exception as e:
            if _is_not_found(e):
                return None
            raise

    @staticmethod
    def _pending(st: dict | None) -> list[tuple[str | None, Any]]:
        if not st:
            return []
        intr = st.get("interrupts") or [i for t in st.get("tasks", []) for i in t.get("interrupts", [])]
        return [(i.get("id") or i.get("interrupt_id"), i.get("value")) for i in intr]

    @staticmethod
    def _has_checkpoint(st: dict | None) -> bool:
        if not st:
            return False
        cp = st.get("checkpoint") or {}
        return bool(cp.get("checkpoint_id") or st.get("values") or st.get("next") or st.get("created_at"))

    def _acquire(self, thread_id: str) -> ThreadLock:
        if self._runs.is_alive(thread_id):
            raise Busy()
        lock = ThreadLock.try_acquire(self._url, thread_id)
        if lock is None:
            raise Busy()
        return lock

    def _watch(self, thread_id: str, run_id: str) -> Callable[[], None]:
        """run 이 끝날 때까지 잠금을 쥐고 기다린다. join 대신 짧은 조회를 되풀이한다 (긴 연결이 끊겨도 다시 본다)."""
        def body() -> None:
            deadline = time.monotonic() + self._max_run
            failures = 0
            while time.monotonic() < deadline:
                try:
                    st = self._client.runs.get(thread_id, run_id).get("status")
                    failures = 0
                except Exception:
                    failures += 1
                    log.warning("run 상태 조회 실패 %d회 (thread=%s run=%s)", failures, thread_id, run_id)
                    if failures >= 20:  # Aegra 가 계속 안 보이면 잠금을 오래 쥐지 않는다. 다음 요청은 wait_idle 이 막는다
                        return
                    st = "running"
                if st not in ACTIVE_RUN_STATUSES:
                    return
                time.sleep(self._poll)
            log.error("run 이 %s초 안에 끝나지 않음 (thread=%s) — 잠금만 놓는다. 다음 요청은 wait_idle 이 막는다",
                      self._max_run, thread_id)
        return body

    # -- 계약
    def start(self, period_id: int, thread_id: str) -> dict:
        lock = self._acquire(thread_id)
        try:
            self._client.threads.create(thread_id=thread_id, if_exists="do_nothing",
                                        metadata={"period_id": period_id})
            self._wait_idle(thread_id)
            if self._has_checkpoint(self._get_state(thread_id)):
                raise AlreadyStarted(f"thread {thread_id} はすでに開始されています")
            run = self._client.runs.create(thread_id, self._assistant, input=start_input(period_id),
                                           multitask_strategy="reject")
            self._runs.spawn(thread_id, self._watch(thread_id, run["run_id"]), lock)
        except BaseException:
            lock.release()
            raise
        return run_status(period_id, thread_id, "running")

    def resume(self, period_id: int, thread_id: str, payload: dict) -> dict:
        lock = self._acquire(thread_id)
        try:
            self._wait_idle(thread_id)
            st, pending = self._checkpoint_status(period_id, thread_id)
            if st["state"] not in WAIT_STATE.values():
                raise NotWaiting(f"再開できる待ちがありません(state={st['state']})")
            iid, value = pending[0]
            match_payload(value, payload)
            command = {"resume": {iid: payload}} if iid else {"resume": payload}  # = Command(resume=…)
            run = self._client.runs.create(thread_id, self._assistant, command=command, multitask_strategy="reject")
            self._runs.spawn(thread_id, self._watch(thread_id, run["run_id"]), lock)
        except BaseException:
            lock.release()
            raise
        return run_status(period_id, thread_id, "running")

    def status(self, period_id: int, thread_id: str | None) -> dict:
        if not thread_id:
            return run_status(period_id, None, "not_started")
        try:
            thread = self._client.threads.get(thread_id)
        except Exception as e:
            if _is_not_found(e):
                return run_status(period_id, thread_id, "not_started")
            raise
        if self._runs.is_alive(thread_id) or self._active_runs(thread_id):
            return run_status(period_id, thread_id, "running")
        return self._checkpoint_status(period_id, thread_id, thread)[0]

    def _checkpoint_status(self, period_id: int, thread_id: str,
                           thread: dict | None = None) -> tuple[dict, list[tuple[str | None, Any]]]:
        """thread state + 마지막 run → RunStatus 와 열린 대기. 도는 중인지는 부르는 쪽이 먼저 본다."""
        st = self._get_state(thread_id)
        if thread is None:
            try:
                thread = self._client.threads.get(thread_id)
            except Exception as e:
                if not _is_not_found(e):
                    raise
        run_error = None
        try:
            last = self._client.runs.list(thread_id, limit=1)
        except Exception as e:
            if not _is_not_found(e):
                raise
            last = []
        if last and last[0].get("status") in ("error", "timeout"):
            run_error = last[0].get("error_message") or last[0].get("error") or f"run {last[0].get('status')}"
        elif (thread or {}).get("status") == "error":
            run_error = "Aegra thread status=error"
        pending = self._pending(st)
        tasks = (st or {}).get("tasks", [])
        status = status_from_parts(
            period_id, thread_id, has_checkpoint=self._has_checkpoint(st), interrupts=pending,
            task_errors=[str(t["error"]) for t in tasks if t.get("error")],
            next_nodes=(st or {}).get("next") or [], run_error=run_error)
        return status, pending


# ---------------------------------------------------------------- 입구

_RUNNER: GraphRunner | None = None
_RUNNER_MU = threading.Lock()


def get_runner() -> GraphRunner:
    """프로세스당 1개 (같은 프로세스의 run 기록을 요청끼리 나눠 써야 한다). RUNNER=local | aegra (app.config)."""
    global _RUNNER
    with _RUNNER_MU:
        if _RUNNER is None:
            s = get_settings()
            if s.runner == "local":
                _RUNNER = LocalRunner(s.database_url)
            elif s.runner == "aegra":
                _RUNNER = AegraRunner(s.aegra_url, s.database_url)
            else:
                raise ValueError(f"RUNNER 는 local 또는 aegra: {s.runner!r}")
        return _RUNNER


def reset_runner() -> None:
    """시험 · 종료용. 다음 get_runner() 가 설정을 다시 읽는다."""
    global _RUNNER
    with _RUNNER_MU:
        if _RUNNER is not None and hasattr(_RUNNER, "close"):
            _RUNNER.close()
        _RUNNER = None
