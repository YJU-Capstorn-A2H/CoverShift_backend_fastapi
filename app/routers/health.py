"""動作確認用。もとの main.py にあった2つのAPIを、そのまま移した。"""
from fastapi import APIRouter, Depends
from app.core.db import get_conn

router = APIRouter(tags=["health"])


# ヘルスチェック用のエンドポイント
@router.get("/health")
def health():
    return {"status": "ok"}


# DB接続のヘルスチェック
@router.get("/health/db")
def health_db(conn=Depends(get_conn)):
    row = conn.execute("SELECT 1 AS result").fetchone()
    return {"db": "ok", "result": row["result"]}