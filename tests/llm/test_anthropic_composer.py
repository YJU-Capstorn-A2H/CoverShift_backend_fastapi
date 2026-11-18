"""AnthropicComposer — 가짜 클라이언트를 넣어(의존성 주입) 네트워크 없이 본다."""
from __future__ import annotations

import dataclasses
import json
import time
from datetime import date, time as dtime
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.domain import Assignment, Violation, ViolationGroup
from app.llm.anthropic_composer import FALLBACK_BETA, AnthropicComposer
from tests.llm.fakeclient import (
    FakeClient,
    FakeStream,
    connection_error,
    message,
    rate_limit_error,
    sdk_timeout_error,
    status_error,
)

GOOD = {"assignments": [{"staff_id": 1, "date": "2026-11-02", "start": "08:00", "end": "17:00"},
                        {"staff_id": 3, "date": "2026-11-02", "start": "17:00", "end": "22:00"}],
        "shortages": [{"date": "2026-11-20", "start": "08:00", "end": "09:00", "need": 3, "have": 2, "reason": "x"}],
        "notes": ["ok"]}


@pytest.fixture
def settings():
    return dataclasses.replace(get_settings(), llm_mode="real", llm_model="claude-sonnet-5-5",
                               llm_max_tokens=64000, llm_timeout_sec=600)


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "logs" / "llm_calls.jsonl"


def _composer(factory, settings, log_path, **kw):
    client = FakeClient(factory)
    return AnthropicComposer(client, settings=settings, log_path=log_path, **kw), client


def _log_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _compose(c, ctx, **kw):
    return c.compose(ctx, previous=kw.get("previous"), history=kw.get("history", []), instruction=kw.get("instruction"))


# ---------------------------------------------------------------- 성공 · 요청 모양

@pytest.mark.req("M-1")
def test_success_returns_draft_and_records_usage(small_ctx, settings, log_path):
    c, client = _composer(lambda kw: FakeStream(message(GOOD)), settings, log_path)
    r = _compose(c, small_ctx)
    assert r.ok and r.error is None and len(r.assignments) == 2
    assert r.shortages[0]["work_date"] == "2026-11-20"
    u = r.usage
    for k in ("seconds", "input_tokens", "output_tokens", "cache_read_input_tokens", "model", "stop_reason", "request_id"):
        assert k in u, k
    assert (u["input_tokens"], u["output_tokens"], u["cache_read_input_tokens"]) == (1200, 3400, 800)
    assert u["model"] == "claude-sonnet-5-5" and u["stop_reason"] == "end_turn" and u["request_id"] == "req_fake_123"
    assert u["cost_usd_est"] == pytest.approx((1200 * 2 + 3400 * 10 + 800 * 0.1 + 100 * 2.5) / 1e6)
    lines = _log_lines(log_path)
    assert len(lines) == 1 and lines[0]["kind"] == "compose" and lines[0]["ok"] is True
    assert lines[0]["n_assignments"] == 2 and lines[0]["period_id"] == 7


@pytest.mark.req("M-1")
def test_request_shape_matches_measured_config(small_ctx, settings, log_path):
    c, client = _composer(lambda kw: FakeStream(message(GOOD)), settings, log_path)
    _compose(c, small_ctx)
    path, kw = client.calls[0]
    assert path == "beta.messages"  # 서버 쪽 fallback 을 켠 기본값
    assert kw["model"] == "claude-sonnet-5-5" and kw["max_tokens"] == 64000
    assert kw["betas"] == [FALLBACK_BETA] and kw["fallbacks"] == "default"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in kw and not ({"temperature", "top_p", "top_k"} & set(kw))  # 측정 구성: 생각 설정 안 줌
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"][0]["role"] == "user"


@pytest.mark.req("M-1")
def test_fallbacks_can_be_switched_off(small_ctx, settings, log_path):
    c, client = _composer(lambda kw: FakeStream(message(GOOD)), settings, log_path, use_fallbacks=False)
    assert _compose(c, small_ctx).ok
    path, kw = client.calls[0]
    assert path == "messages" and "betas" not in kw and "fallbacks" not in kw


@pytest.mark.req("F-5")
def test_retry_call_sends_previous_and_history(small_ctx, settings, log_path):
    c, client = _composer(lambda kw: FakeStream(message(GOOD)), settings, log_path)
    v = Violation("QUAL_MISSING", "required", date(2026, 11, 2), None, {"time": "08:00", "need": 1, "have": 0})
    g = ViolationGroup(key=v.key, check_code=v.check_code, severity="required", rounds=(1,), example=v)
    _compose(c, small_ctx, previous=[Assignment(2, date(2026, 11, 2), "work", dtime(8), dtime(17))], history=[g])
    content = client.calls[0][1]["messages"][0]["content"]
    assert len(content) == 2 and "前回の案" in content[1]["text"] and "【1回目】" in content[1]["text"]
    assert _log_lines(log_path)[0]["retry"] is True and _log_lines(log_path)[0]["history_groups"] == 1


@pytest.mark.req("M-1")
def test_fallback_continuation_text_is_joined(small_ctx, settings, log_path):
    """중간 fallback: 앞 모델의 부분 text + fallback 블록 + 이어 쓴 text → 이어 붙여 읽는다 (실제 API 모양은 미검증)."""
    full = json.dumps(GOOD, ensure_ascii=False)
    blocks = [SimpleNamespace(type="text", text=full[:40]), SimpleNamespace(type="fallback"),
              SimpleNamespace(type="text", text=full[40:])]
    msg = message(None, extra_blocks=blocks, model="claude-sonnet-5")
    c, _ = _composer(lambda kw: FakeStream(msg), settings, log_path)
    r = _compose(c, small_ctx)
    assert r.ok and len(r.assignments) == 2
    assert r.usage["fallback_used"] is True and r.usage["model"] == "claude-sonnet-5"
    assert r.usage["cost_usd_est"] is None  # 단가 표에 없는 모델은 비용을 지어내지 않는다


# ---------------------------------------------------------------- 실패는 전부 ok=False (F-4)

@pytest.mark.req("F-4")
def test_max_tokens_is_failure_without_reading_content(small_ctx, settings, log_path):
    c, _ = _composer(lambda kw: FakeStream(message(GOOD, stop_reason="max_tokens")), settings, log_path)
    r = _compose(c, small_ctx)
    assert not r.ok and r.error == "max_tokens" and r.assignments == []
    assert _log_lines(log_path)[0]["error"] == "max_tokens"


@pytest.mark.req("F-4")
def test_refusal_is_failure_and_category_is_logged(small_ctx, settings, log_path):
    c, _ = _composer(lambda kw: FakeStream(message(None, stop_reason="refusal", category="general_harms")), settings, log_path)
    r = _compose(c, small_ctx)
    assert not r.ok and r.error == "refusal"
    assert _log_lines(log_path)[0]["refusal_category"] == "general_harms"


@pytest.mark.req("F-4")
@pytest.mark.parametrize("text,error", [("not json", "parse_error"), ("", "parse_error"),
                                        ('{"assignments":[],"shortages":[],"notes":[]}', "empty")])
def test_unreadable_or_empty_output(small_ctx, settings, log_path, text, error):
    c, _ = _composer(lambda kw: FakeStream(message(None, text=text)), settings, log_path)
    r = _compose(c, small_ctx)
    assert not r.ok and r.error == error


@pytest.mark.req("F-4")
@pytest.mark.parametrize("exc,error", [
    (rate_limit_error, "api_error:rate_limit"),
    (lambda: status_error(500), "api_error:status_500"),
    (lambda: status_error(400), "api_error:status_400"),
    (connection_error, "api_error:connection"),
    (sdk_timeout_error, "timeout"),
    (lambda: RuntimeError("boom"), "unexpected:RuntimeError"),
])
def test_api_errors_never_raise(small_ctx, settings, log_path, exc, error):
    c, _ = _composer(lambda kw: FakeStream(None, raise_on_enter=exc()), settings, log_path)
    r = _compose(c, small_ctx)  # 예외가 밖으로 나오면 이 줄에서 시험이 실패한다
    assert not r.ok and r.error == error
    line = _log_lines(log_path)[0]
    assert line["error"] == error and line["ok"] is False
    if error == "api_error:rate_limit":
        assert line["request_id"] == "req_429"


# ---------------------------------------------------------------- 전체 시간 한도 (F-13)

@pytest.mark.req("F-13")
def test_total_deadline_cuts_a_slow_event_stream(small_ctx, settings, log_path):
    """이벤트는 계속 오지만 전체가 한도를 넘는 경우: 루프 안 검사로 끊는다."""
    c, client = _composer(lambda kw: FakeStream(message(GOOD), n_events=1000, delay=0.02), settings, log_path,
                          timeout_sec=0.2)
    t0 = time.monotonic()
    r = _compose(c, small_ctx)
    took = time.monotonic() - t0
    assert not r.ok and r.error == "timeout"
    assert took < 1.0, took
    assert client.streams[0].events_sent < 1000
    assert client.streams[0].closed.wait(1.0)  # 스트림을 닫았다
    assert _log_lines(log_path)[0]["error"] == "timeout"


@pytest.mark.req("F-13")
def test_total_deadline_cuts_a_silent_stream(small_ctx, settings, log_path):
    """이벤트가 하나도 오지 않는 경우(SDK 는 ping 을 넘긴다): 기다리는 쪽이 한도에서 돌아오고 스트림을 닫는다."""
    c, client = _composer(lambda kw: FakeStream(message(GOOD), block=True), settings, log_path, timeout_sec=0.2)
    t0 = time.monotonic()
    r = _compose(c, small_ctx)
    assert not r.ok and r.error == "timeout" and time.monotonic() - t0 < 1.0
    assert client.streams[0].closed.wait(1.0)


@pytest.mark.req("F-13")
def test_total_deadline_cuts_a_slow_connect(small_ctx, settings, log_path):
    """연결 · 첫 응답이 늦는 경우(SDK 재시도 포함): compose() 는 한도에서 돌아온다."""
    c, _ = _composer(lambda kw: FakeStream(message(GOOD), enter_delay=2.0), settings, log_path, timeout_sec=0.2)
    t0 = time.monotonic()
    r = _compose(c, small_ctx)
    assert r.error == "timeout" and time.monotonic() - t0 < 1.0


@pytest.mark.req("F-13")
def test_fast_stream_within_deadline_is_not_cut(small_ctx, settings, log_path):
    """깨뜨리는 쪽의 짝: 한도 안이면 끊지 않는다 (한도 검사가 항상 timeout 을 내는 것이 아님을 확인)."""
    c, _ = _composer(lambda kw: FakeStream(message(GOOD), n_events=5, delay=0.01), settings, log_path, timeout_sec=2.0)
    assert _compose(c, small_ctx).ok


@pytest.mark.req("F-13")
def test_default_timeout_comes_from_settings(settings, log_path):
    c = AnthropicComposer(settings=dataclasses.replace(settings, llm_timeout_sec=123), log_path=log_path)
    assert c._timeout == 123.0 and c._client is None  # 클라이언트는 첫 호출 때 만든다 (만들 때 네트워크 없음)


# ---------------------------------------------------------------- 점장 지시 → 틀 규칙 (M-2, T-4)

@pytest.mark.req("T-4", "M-2")
def test_instruction_to_rules_validates_each_rule(small_ctx, settings, log_path):
    nulls = dict.fromkeys(["staff_id", "weekday", "date", "shift", "limit", "count", "start", "end"])
    payload = {"rules": [
        {"template_key": "not_available", "params": {**nulls, "staff_id": 3, "weekday": 7}, "source_text": "3番は日曜不可"},
        {"template_key": "min_headcount", "params": {**nulls, "count": 3, "start": "17:15", "end": "22:30"},
         "source_text": "17:15から3人"},  # 30분 단위 아님 → 거부
        {"template_key": "not_available", "params": {**nulls, "staff_id": 3, "weekday": 7, "limit": 2},
         "source_text": "섞인 params"},  # 틀에 없는 칸(limit) → 거부
        {"template_key": "max_hours", "params": nulls, "source_text": "週20時間まで"},  # 틀 이름 없음 → 거부
        {"template_key": "not_available", "params": {**nulls, "staff_id": 99, "weekday": 1}, "source_text": "99番"},
        {"template_key": "max_consecutive", "params": {**nulls, "limit": 4}, "source_text": "連勤は4日まで"},
    ], "free_text": ["ベテランと新人を組ませて", "  "]}
    c, client = _composer(lambda kw: FakeStream(message(payload)), settings, log_path)
    res = c.instruction_to_rules(small_ctx, "...")
    assert [(r.rule_key, r.template_key) for r in res.rules] == [("r1", "not_available"), ("r2", "max_consecutive")]
    assert res.rules[0].params.staff_id == 3 and res.rules[0].params.weekday == 7
    assert res.rules[1].params.shift == "any" and res.rules[1].params.limit == 4
    assert res.free_text == ["ベテランと新人を組ませて"]
    reasons = [r["reason"] for r in res.rejected]
    assert len(reasons) == 4
    assert "30분" in reasons[0] and "limit" in reasons[1] and "unknown template_key" in reasons[2]
    assert "unknown staff_id: 99" in reasons[3]
    assert client.calls[0][1]["max_tokens"] == 16000
    assert _log_lines(log_path)[0]["kind"] == "instruction" and _log_lines(log_path)[0]["n_rejected"] == 4


@pytest.mark.req("T-4")
def test_instruction_llm_failure_keeps_text_as_free_text(small_ctx, settings, log_path):
    c, _ = _composer(lambda kw: FakeStream(None, raise_on_enter=status_error(503)), settings, log_path)
    res = c.instruction_to_rules(small_ctx, "3番は日曜不可")
    assert res.rules == [] and res.free_text == ["3番は日曜不可"]
    assert res.rejected == [{"raw": None, "reason": "llm_error:api_error:status_503"}]
