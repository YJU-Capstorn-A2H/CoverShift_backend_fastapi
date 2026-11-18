"""DB 연결 (메인 세션 소유). psycopg 3, dict 행."""
from __future__ import annotations

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


@contextmanager
def connect(url: str | None = None, *, autocommit: bool = False):
    with psycopg.connect(url or get_settings().database_url, row_factory=dict_row, autocommit=autocommit) as c:
        yield c
