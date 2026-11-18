"""AnthropicComposer 시험용 가짜 클라이언트 (네트워크 없음). client.messages.stream / client.beta.messages.stream 모양만 흉내 낸다."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import anthropic
import httpx2


def message(payload: object | None = None, *, stop_reason: str = "end_turn", text: str | None = None,
            category: str | None = None, model: str = "claude-sonnet-5-5", extra_blocks: list | None = None):
    blocks = list(extra_blocks or [])
    body = text if text is not None else (json.dumps(payload, ensure_ascii=False) if payload is not None else None)
    if body is not None:
        blocks.append(SimpleNamespace(type="text", text=body))
    return SimpleNamespace(
        model=model, stop_reason=stop_reason, content=blocks, _request_id="req_fake_123",
        stop_details=SimpleNamespace(type="refusal", category=category, explanation=None) if stop_reason == "refusal" else None,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=3400, cache_read_input_tokens=800,
                              cache_creation_input_tokens=100),
    )


class FakeStream:
    def __init__(self, msg, *, n_events: int = 3, delay: float = 0.0, block: bool = False, enter_delay: float = 0.0,
                 raise_on_enter: BaseException | None = None):
        self.msg, self.n_events, self.delay, self.block = msg, n_events, delay, block
        self.enter_delay, self.raise_on_enter = enter_delay, raise_on_enter
        self.closed = threading.Event()
        self.events_sent = 0
        self.request_id = "req_from_stream"

    def __enter__(self):
        if self.enter_delay:
            time.sleep(self.enter_delay)
        if self.raise_on_enter is not None:
            raise self.raise_on_enter
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __iter__(self):
        if self.block:  # 이벤트가 오지 않는 스트림 (ping 만 오는 긴 생각 시간 흉내) — close() 될 때까지 멈춤
            self.closed.wait(10)
            raise httpx2.ReadError("stream closed")
        for _ in range(self.n_events):
            if self.closed.is_set():
                raise httpx2.ReadError("stream closed")
            if self.delay:
                time.sleep(self.delay)
            self.events_sent += 1
            yield SimpleNamespace(type="content_block_delta")

    def get_final_message(self):
        return self.msg

    def close(self):
        self.closed.set()


class _Messages:
    def __init__(self, owner, name):
        self._owner, self._name = owner, name

    def stream(self, **kwargs):
        self._owner.calls.append((self._name, kwargs))
        s = self._owner.factory(kwargs)
        self._owner.streams.append(s)
        return s


class FakeClient:
    """factory(kwargs) → FakeStream. calls 에 (경로, kwargs) 를 남긴다."""

    def __init__(self, factory):
        self.factory = factory
        self.calls: list[tuple[str, dict]] = []
        self.streams: list[FakeStream] = []
        self.messages = _Messages(self, "messages")
        self.beta = SimpleNamespace(messages=_Messages(self, "beta.messages"))


def _req():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def rate_limit_error():
    return anthropic.RateLimitError("rate limited", response=httpx2.Response(429, request=_req(), headers={"request-id": "req_429"}), body=None)


def status_error(code: int = 500):
    return anthropic.APIStatusError("server error", response=httpx2.Response(code, request=_req()), body=None)


def connection_error():
    return anthropic.APIConnectionError(request=_req())


def sdk_timeout_error():
    return anthropic.APITimeoutError(request=_req())
