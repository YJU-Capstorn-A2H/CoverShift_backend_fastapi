"""측정 기록 (KPI: 편성 시간 · 비용, notes/2026-11-10-구현-진행계획-KPI.md 2장).

- 호출 1번 = JSON 1줄을 logs/llm_calls.jsonl 에 덧붙인다. 프롬프트 본문 · 직원 데이터는 쓰지 않는다 (숫자와 상태만).
- 기록이 실패해도 편성은 멈추지 않는다 (디스크 문제로 LLM 결과를 버리지 않기 위해). 대신 stderr 에 한 줄.
- 비용은 추정값이다. 단가 출처: claude-api 스킬 모델 표(2026-11-03 캐시) — Sonnet 5.5 입력 $2 · 출력 $10 /MTok,
  캐시 읽기 $0.10. 캐시 쓰기는 입력의 1.25배로 계산(5분 TTL). 실제 청구와 대조하지 않았다.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

DEFAULT_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "llm_calls.jsonl"

# 모델 → (입력, 출력, 캐시 읽기, 캐시 쓰기) $/MTok
PRICES: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.5),
}


def estimate_cost(model: str | None, usage: dict) -> float | None:
    p = PRICES.get(model or "")
    if p is None:
        return None
    pin, pout, pread, pwrite = p
    cost = ((usage.get("input_tokens") or 0) * pin + (usage.get("output_tokens") or 0) * pout
            + (usage.get("cache_read_input_tokens") or 0) * pread
            + (usage.get("cache_creation_input_tokens") or 0) * pwrite) / 1e6
    return round(cost, 5)


def append_call_log(path: Path | None, record: dict) -> None:
    if path is None:
        return
    line = {"ts": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), **record}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        print(f"[llm] 측정 기록 실패 ({path}): {e}", file=sys.stderr)
