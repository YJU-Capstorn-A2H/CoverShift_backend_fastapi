"""動作確認用。もとの main.py にあった2つのAPIを、そのまま移した。"""
from fastapi import APIRouter, Depends
from app.core.db import get_conn

router = APIRouter(tags=["health"])


# ヘルスチェック用のエンドポイント
# 仕様書V9 (V9に記載なし)｜動作確認用 / 명세서 V9 (V9에 없음)｜동작 확인용
@router.get("/health")
def health():
    return {"status": "ok"}


# DB接続のヘルスチェック
# 仕様書V9 (V9に記載なし)｜DB接続の確認用 / 명세서 V9 (V9에 없음)｜DB 연결 확인용
@router.get("/health/db")
def health_db(conn=Depends(get_conn)):
    row = conn.execute("SELECT 1 AS result").fetchone()
    return {"db": "ok", "result": row["result"]}