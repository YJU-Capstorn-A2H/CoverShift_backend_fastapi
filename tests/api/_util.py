"""시험 공통 값 · 도우미."""
from __future__ import annotations

FUTURE = "2099-01-01T00:00:00+09:00"
PAST = "2000-01-01T00:00:00+09:00"
PW = "password-123"
BUSY_MESSAGE = "いま編成を処理しています"  # V9_ko 509 — 화면 문장이므로 문자열 그대로 비교한다


def err(r) -> tuple[int, str]:
    """오류 응답 모양(A-19)을 확인하고 (상태, 코드)를 돌려준다."""
    body = r.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message"}, body
    assert body["error"]["code"].replace("_", "").isupper() and body["error"]["message"], body
    return r.status_code, body["error"]["code"]
