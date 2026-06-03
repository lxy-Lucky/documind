"""SQLite connection helper with sqlite-vec extension loaded.

Usage:
    with get_db() as conn:
        conn.execute("...")
        conn.commit()

The connection is per-call (no global pool). SQLite is single-writer; the
WAL mode + short-lived connections is the simplest safe pattern.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import sqlite_vec
from loguru import logger

from config import settings


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = _connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Apply schema.sql + create the sqlite-vec virtual table for chunk embeddings.

    Idempotent: safe to call on every startup.
    """
    schema_path = Path(__file__).parent / "schema.sql"
    with get_db() as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        # Idempotent column additions for existing DBs upgrading in place.
        # (SQLite supports ALTER TABLE ADD COLUMN; we ignore "duplicate column" errors.)
        for stmt in (
            "ALTER TABLE document ADD COLUMN enrich_status TEXT DEFAULT 'pending'",
        ):
            try:
                conn.execute(stmt)
            except Exception:
                pass
        # vec0 virtual table cannot be created via plain CREATE TABLE IF NOT EXISTS
        # inside schema.sql reliably across versions; do it here.
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                embedding FLOAT[1024]
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS summary_vec USING vec0(
                summary_id INTEGER PRIMARY KEY,
                embedding FLOAT[1024]
            )
            """
        )
        conn.commit()
    logger.info(f"Database initialized at {settings.db_path}")
