"""진짜 LLM (Anthropic API 직접, V9_ko 482 · M8).

호출 모양 — 11/08 mix1_55(유일하게 통과한 측정 구성)와 같게:
- 모델 = settings.llm_model (기본 claude-sonnet-5-5). thinking 파라미터를 보내지 않는다
  (Sonnet 5.5 는 생략 = adaptive, {"type":"disabled"} 는 400). temperature/top_p/top_k 도 보내지 않는다.
- 구조화 출력: output_config.format = json_schema (schema.py). 스트리밍 + get_final_message().
- 거절(refusal) 대비: 서버 쪽 fallback (beta server-side-fallback-2026-07-01, fallbacks="default").
  설치된 SDK(anthropic 1.9.0)의 client.beta.messages.stream 에 fallbacks · output_config · betas 가
  이름 있는 파라미터로 있는 것을 확인했다 (inspect.signature). 실제 API 에서 받아 주는지는 확인하지 않았다(미검증).

시간 한도 (F-13, V9_ko 488): 스트리밍의 timeout 은 「읽기 1번」 기준이고 SDK 는 ping 을 넘겨 버려서
루프 안 검사만으로는 조용한 스트림을 못 끊는다. 그래서 스트림을 작업 스레드에서 돌리고, 부르는 쪽은
남은 시간만큼만 기다린다(join). 넘으면 스트림을 닫고 ok=False, error="timeout" 으로 바로 돌아온다.
SDK 재시도(max_retries=1)가 있어도 compose() 가 돌아오는 시각은 한도를 넘지 않는다.

compose() 는 예외를 밖으로 내지 않는다 — 실패는 전부 ComposeResult(ok=False) 로 (F-4: 그래프가 재편성 1회로 센다).
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import anthropic

from app.config import Settings, get_settings
from app.domain import Assignment, ComposeResult, InstructionResult, PeriodContext, ViolationGroup
from app.llm.prompt import build_compose_request, build_instruction_request
from app.llm.schema import draft_schema, finalize_rules, instruction_schema, parse_draft_text
from app.llm.usage_log import DEFAULT_LOG_PATH, append_call_log, estimate_cost

USE_FALLBACKS = True  # False 면 일반 client.messages.stream (beta · fallbacks 없음)
FALLBACK_BETA = "server-side-fallback-2026-07-01"
INSTRUCTION_MAX_TOKENS = 16000  # 지시문 변환은 짧다. settings.llm_max_tokens 보다 크면 그쪽을 쓴다


class AnthropicComposer:
    """client 를 넣으면(테스트) 그것을 쓰고, 없으면 첫 호출 때 anthropic.Anthropic() 을 만든다."""

    def __init__(self, client: object | None = None, *, settings: Settings | None = None,
                 timeout_sec: float | None = None, log_path: Path | None = DEFAULT_LOG_PATH,
                 use_fallbacks: bool | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._timeout = float(timeout_sec if timeout_sec is not None else self._settings.llm_timeout_sec)
        self._log_path = log_path
        self._use_fallbacks = USE_FALLBACKS if use_fallbacks is None else use_fallbacks
        self._clock = clock

    # ------------------------------------------------------------ 공개 메서드 (Composer)

    def compose(self, ctx: PeriodContext, *, previous: Sequence[Assignment] | None,
                history: Sequence[ViolationGroup], instruction: str | None) -> ComposeResult:
        meta = {"kind": "compose", "period_id": ctx.period.period_id, "retry": previous is not None,
                "history_groups": len(history), "has_instruction": bool(instruction and instruction.strip())}
        try:
            system, messages = build_compose_request(ctx, previous, history, instruction)
            schema = draft_schema(ctx)
        except Exception as e:  # 프롬프트를 못 만들면 호출하지 않고 실패로 (예외를 밖으로 내지 않는다)
            usage = {**meta, "error": f"unexpected:{type(e).__name__}", "seconds": 0.0}
            append_call_log(self._log_path, usage)
            return ComposeResult(ok=False, error=usage["error"], usage=usage)

        text, usage, error = self._call(system, messages, schema, self._settings.llm_max_tokens, meta)
        if error is not None:
            result = ComposeResult(ok=False, error=error)
        else:
            result = parse_draft_text(ctx, text or "")
            usage.update(result.usage)  # n_rows_in · n_dropped
        usage.update(ok=result.ok, error=result.error, n_assignments=len(result.assignments),
                     n_shortages=len(result.shortages))
        result.usage = usage
        append_call_log(self._log_path, usage)
        return result

    def instruction_to_rules(self, ctx: PeriodContext, text: str) -> InstructionResult:
        """M-2 (그래프 밖, 1회 · G18). 실패하면 원문 전체를 free_text 로 두고 rejected 에 이유를 남긴다 (가정 신규)."""
        meta = {"kind": "instruction", "period_id": ctx.period.period_id}

        def _fallback(reason: str, usage: dict) -> InstructionResult:
            usage.update(ok=False, error=reason)
            append_call_log(self._log_path, usage)
            free = [text.strip()] if text and text.strip() else []
            return InstructionResult(rules=[], free_text=free, rejected=[{"raw": None, "reason": f"llm_error:{reason}"}])

        try:
            system, messages = build_instruction_request(ctx, text)
        except Exception as e:
            return _fallback(f"unexpected:{type(e).__name__}", {**meta, "seconds": 0.0})
        max_tokens = min(self._settings.llm_max_tokens, INSTRUCTION_MAX_TOKENS)
        out, usage, error = self._call(system, messages, instruction_schema(), max_tokens, meta)
        if error is not None:
            return _fallback(error, usage)
        try:
            data = json.loads(out or "")
            candidates, free_text = data["rules"], data["free_text"]
            if not isinstance(candidates, list) or not isinstance(free_text, list):
                raise TypeError("rules/free_text must be lists")
        except (ValueError, KeyError, TypeError):
            return _fallback("parse_error", usage)
        result = finalize_rules(ctx, candidates, free_text)
        usage.update(ok=True, error=None, n_rules=len(result.rules), n_rejected=len(result.rejected),
                     n_free_text=len(result.free_text))
        append_call_log(self._log_path, usage)
        return result

    # ------------------------------------------------------------ 안쪽

    def _get_client(self):
        if self._client is None:
            # 키는 환경 변수(ANTHROPIC_API_KEY)에서 SDK 가 읽는다. 코드에 쓰지 않는다 (V9_ko 511)
            self._client = anthropic.Anthropic(timeout=self._timeout, max_retries=1)
        return self._client

    def _request_kwargs(self, system: list[dict], messages: list[dict], schema: dict, max_tokens: int) -> dict:
        kw = {"model": self._settings.llm_model, "max_tokens": max_tokens, "system": system, "messages": messages,
              "output_config": {"format": {"type": "json_schema", "schema": schema}}}
        if self._use_fallbacks:
            kw.update(betas=[FALLBACK_BETA], fallbacks="default")
        return kw

    def _stream_fn(self):
        c = self._get_client()
        return c.beta.messages.stream if self._use_fallbacks else c.messages.stream

    def _call(self, system, messages, schema, max_tokens, meta) -> tuple[str | None, dict, str | None]:
        """→ (본문 JSON 글자 | None, usage, error | None). 예외를 내지 않는다."""
        usage: dict = {**meta, "requested_model": self._settings.llm_model, "timeout_sec": self._timeout}
        t0 = self._clock()
        text: str | None = None
        error: str | None = None
        try:
            kwargs = self._request_kwargs(system, messages, schema, max_tokens)
            msg, stream, timed_out = self._stream_with_deadline(self._stream_fn(), kwargs, t0 + self._timeout)
            if timed_out:
                error = "timeout"
            else:
                text, error = self._read_message(msg, stream, usage)
        except anthropic.RateLimitError as e:
            error = "api_error:rate_limit"
            usage["request_id"] = getattr(e, "request_id", None)
        except anthropic.APIStatusError as e:
            error = f"api_error:status_{e.status_code}"
            usage["request_id"] = getattr(e, "request_id", None)
        except anthropic.APITimeoutError:  # APIConnectionError 의 하위 클래스라 먼저 잡는다
            error = "timeout"
        except anthropic.APIConnectionError:
            error = "api_error:connection"
        except Exception as e:  # compose() 는 예외를 내지 않는다는 약속. 종류는 기록에 남긴다
            error = f"unexpected:{type(e).__name__}"
        usage["seconds"] = round(self._clock() - t0, 2)
        usage["error"] = error
        return text, usage, error

    def _stream_with_deadline(self, stream_fn, kwargs: dict, deadline: float):
        """작업 스레드에서 스트림을 끝까지 읽는다. 기한을 넘으면 닫고 (None, stream, True)."""
        cancel = threading.Event()
        box: dict = {}

        def worker() -> None:
            try:
                with stream_fn(**kwargs) as stream:
                    box["stream"] = stream
                    if cancel.is_set():
                        return
                    for _event in stream:
                        if cancel.is_set() or self._clock() > deadline:
                            box["late"] = True
                            return  # with 를 나가면서 응답을 닫는다
                    box["message"] = stream.get_final_message()
            except BaseException as e:  # noqa: BLE001 — 부르는 쪽 스레드에서 다시 던진다
                box["error"] = e

        th = threading.Thread(target=worker, name="llm-stream", daemon=True)
        th.start()
        th.join(timeout=max(0.0, deadline - self._clock()))
        if th.is_alive():
            cancel.set()
            s = box.get("stream")
            if s is not None:
                try:
                    s.close()
                except Exception:  # noqa: BLE001 — 닫다가 난 오류는 결과(timeout)를 바꾸지 않는다
                    pass
            return None, s, True
        if "error" in box:
            if cancel.is_set() or self._clock() > deadline:
                return None, box.get("stream"), True
            raise box["error"]
        if box.get("late"):
            return None, box.get("stream"), True
        return box.get("message"), box.get("stream"), False

    def _read_message(self, msg, stream, usage: dict) -> tuple[str | None, str | None]:
        """stop_reason 을 먼저 본다 (max_tokens · refusal 이면 본문을 읽지 않는다)."""
        u = getattr(msg, "usage", None)
        usage.update(
            model=getattr(msg, "model", None),
            stop_reason=getattr(msg, "stop_reason", None),
            request_id=getattr(msg, "_request_id", None) or getattr(stream, "request_id", None),
            input_tokens=getattr(u, "input_tokens", None),
            output_tokens=getattr(u, "output_tokens", None),
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", None) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", None) or 0,
        )
        blocks = list(getattr(msg, "content", None) or [])
        usage["fallback_used"] = any(getattr(b, "type", None) == "fallback" for b in blocks)
        usage["cost_usd_est"] = estimate_cost(usage["model"], usage)
        sr = usage["stop_reason"]
        if sr == "max_tokens":
            return None, "max_tokens"
        if sr == "refusal":
            usage["refusal_category"] = getattr(getattr(msg, "stop_details", None), "category", None)
            return None, "refusal"
        # 중간 fallback 이면 앞 모델의 부분 text + fallback 블록 + 이어 쓴 text 순서다 → text 를 전부 이어 붙인다.
        # 이어 붙인 것이 JSON 이 아니면 마지막 fallback 블록 뒤의 text 만 쓴다 (둘 다 실제 API 로는 미검증)
        def _join(bs) -> str:
            return "".join(getattr(b, "text", "") or "" for b in bs if getattr(b, "type", None) == "text")

        text = _join(blocks)
        if usage["fallback_used"]:
            try:
                json.loads(text)
            except ValueError:
                last = max(i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback")
                text = _join(blocks[last + 1:])
        if not text.strip():
            return None, "parse_error"
        return text, None
