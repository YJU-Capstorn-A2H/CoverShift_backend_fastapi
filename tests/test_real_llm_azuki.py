"""本物の Claude で、アズキのグラフを1回だけ通す(お金がかかる: 約$0.45/回)。ふだんは、スキップされる。
動かす:  $env:RUN_REAL_LLM="1"; python -m pytest tests/test_real_llm_azuki.py -q -s -p no:warnings
見るのは「承認待ちか打診待ちまで、たどり着くか」と、その案の中身(違反の数・種類、時間、トークン)。
結果は real_llm_out/ に残す(コミットしない)。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

pytestmark = pytest.mark.skipif(os.environ.get("RUN_REAL_LLM") != "1" or not os.environ.get("ANTHROPIC_API_KEY"),
                                reason="RUN_REAL_LLM=1 と ANTHROPIC_API_KEY が要る(本物のLLMを呼ぶため)")


def test_real_llm_one_pass(clean_db, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # llm_raw_output.txt / month_draft.json の置き場
    monkeypatch.setenv("JWT_SECRET", "e2e-only-secret-0123456789abcdefghij")
    monkeypatch.setenv("DEV_SKIP_AUTH", "false")
    monkeypatch.setenv("RUNNER", "local")
    monkeypatch.setenv("MCP_MODE", "inprocess")
    monkeypatch.setenv("GRAPH_IMPL", "azuki")
    monkeypatch.setenv("DATABASE_URL", clean_db)
    import app.graph_azuki_bridge as bridge

    if bridge._GRAPH_DIR not in sys.path:
        sys.path.append(bridge._GRAPH_DIR)
    import graph as G
    import node.llm as L
    import settings as S

    monkeypatch.setattr(S, "DB_URL", clean_db)
    monkeypatch.setattr(G, "DB_URL", clean_db)
    monkeypatch.setattr(L, "LLM", None)  # 本物を使う(get_llm が作る)

    from app.runner import reset_runner
    from app.seed import seed_db

    reset_runner()
    with psycopg.connect(clean_db, row_factory=dict_row) as c:
        seed_db(c, manager_email="real@example.test", password="real-password-1")
        c.commit()
    from app.api_seung.main import app

    t0 = time.monotonic()
    with TestClient(app) as cl:
        tok = cl.post("/api/auth/manager/login",
                      json={"email": "real@example.test", "password": "real-password-1"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        pid = cl.get("/api/shift-periods", headers=h).json()[0]["period_id"]
        assert cl.post(f"/api/shift-periods/{pid}/start-now", headers=h).status_code == 202
        while True:
            st = cl.get(f"/api/shift-periods/{pid}/run-status", headers=h).json()
            if st["state"] != "running" or time.monotonic() - t0 > 900:
                break
            time.sleep(2)
        sec = time.monotonic() - t0
        out = ROOT / "real_llm_out"
        out.mkdir(exist_ok=True)
        for f in ("llm_raw_output.txt", "month_draft.json"):
            if (tmp_path / f).exists():
                shutil.copy(tmp_path / f, out / f)
        w = st.get("waiting") or {}
        v = w.get("violations") or []
        by = {}
        for x in v:
            by[x["check_code"]] = by.get(x["check_code"], 0) + 1
        summary = {"state": st["state"], "seconds": round(sec), "waiting_type": w.get("type"), "result": w.get("result"),
                   "violations_total": len(v), "violations_by_code": by,
                   "non_waivable": sum(1 for x in v if not x.get("waivable", True)),
                   "blocked": len(w.get("blocked") or []), "notes": (w.get("notes") or [])[:5]}
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n=== 本物のClaudeの結果 ===\n" + json.dumps(summary, ensure_ascii=False, indent=2))
        with psycopg.connect(clean_db) as c:
            print("drafts:", c.execute("SELECT count(*) FROM shift_drafts").fetchone()[0],
                  " assignments(latest):", c.execute("SELECT count(*) FROM shift_assignments WHERE draft_id=(SELECT max(draft_id) FROM shift_drafts)").fetchone()[0])
    reset_runner()
    assert st["state"] in ("waiting_review", "waiting_contact"), st
