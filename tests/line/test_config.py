"""LINE 설정 (A-22): 기본은 stub. line 모드인데 시크릿 · 토큰이 비면 시작할 때 멈춘다."""
from __future__ import annotations

import importlib

import pytest

from app.api.main import create_app
from app.config import ConfigError, get_settings
from app.line_dispatcher import main as dispatcher_main

def aegra_entry() -> None:
    """Aegra 가 하는 것처럼 그래프 파일을 (다시) 읽는다 (aegra.json 의 graphs.shift)."""
    import app.graph.aegra_entry as entry
    importlib.reload(entry)


LINE_VARS = ("LINE_MODE", "LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_OFFICIAL_ACCOUNT_ID")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in LINE_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.req("A-22")
def test_default_is_stub_and_needs_no_secrets():
    s = get_settings()
    assert (s.line_mode, s.line_channel_secret, s.line_channel_access_token, s.line_official_account_id) == (
        "stub", "", "", "")
    create_app()


@pytest.mark.req("A-22")
def test_line_mode_with_both_values_starts(monkeypatch):
    monkeypatch.setenv("LINE_MODE", "line")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "test-secret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("LINE_OFFICIAL_ACCOUNT_ID", "@test-account")
    assert get_settings().line_mode == "line"
    create_app()
    aegra_entry()


@pytest.mark.req("A-22")
@pytest.mark.parametrize("present, missing", [
    ({}, "LINE_CHANNEL_SECRET · LINE_CHANNEL_ACCESS_TOKEN · LINE_OFFICIAL_ACCOUNT_ID"),
    ({"LINE_CHANNEL_SECRET": "test-secret-VALUE", "LINE_OFFICIAL_ACCOUNT_ID": "@id-VALUE"},
     "LINE_CHANNEL_ACCESS_TOKEN"),
    ({"LINE_CHANNEL_ACCESS_TOKEN": "test-token-VALUE", "LINE_CHANNEL_SECRET": "  ",
      "LINE_OFFICIAL_ACCOUNT_ID": "@id-VALUE"}, "LINE_CHANNEL_SECRET"),
    # 공식 계정 ID 가 없으면 연결 링크가 line 모드에서 404 인 스텁 화면을 가리킨다
    ({"LINE_CHANNEL_SECRET": "test-secret-VALUE", "LINE_CHANNEL_ACCESS_TOKEN": "test-token-VALUE"},
     "LINE_OFFICIAL_ACCOUNT_ID"),
])
def test_line_mode_with_missing_values_refuses_to_start(monkeypatch, present, missing):
    monkeypatch.setenv("LINE_MODE", "line")
    for name, value in present.items():
        monkeypatch.setenv(name, value)
    for start in (get_settings, create_app, dispatcher_main, aegra_entry):  # 설정 읽기 · API · 발송기 · Aegra 그래프
        with pytest.raises(ConfigError) as e:
            start()
        assert str(e.value) == f"LINE_MODE=line 인데 {missing} 가 비어 있습니다"
        assert "VALUE" not in str(e.value)  # 이름만 싣고 값은 싣지 않는다


@pytest.mark.req("A-22")
def test_unknown_line_mode_is_refused(monkeypatch):
    monkeypatch.setenv("LINE_MODE", "real")
    with pytest.raises(ConfigError, match="stub \\| line"):
        get_settings()
