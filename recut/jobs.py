"""A job store in SQLite, worked by a background thread.

A run takes 20 to 60 seconds and a browser will not hold a request open that long,
so the API takes a job and hands back an id. No Celery, no Redis, no Docker: this
machine cannot run Docker, and a queue that needs three services to repurpose a blog
post is a worse product than one that needs none.

SQLite is the whole persistence layer. Each write is its own connection, which is
the boring choice that works across threads without a connection pool.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    state        TEXT NOT NULL,
    source       TEXT NOT NULL,
    targets      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    progress     TEXT,
    result       TEXT,
    error        TEXT
);
"""

PENDING, RUNNING, DONE, FAILED = "pending", "running", "done", "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, path: str | Path = "recut.db") -> None:
        self.path = str(path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, source: str, targets: list[str]) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, state, source, targets, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (job_id, PENDING, source, json.dumps(targets), _now(), _now()),
            )
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["targets"] = json.loads(job["targets"])
        job["result"] = json.loads(job["result"]) if job["result"] else None
        return job

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, state, source, targets, created_at, progress"
                " FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**dict(r), "targets": json.loads(r["targets"])} for r in rows]

    def progress(self, job_id: str, message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET state=?, progress=?, updated_at=? WHERE id=?",
                (RUNNING, message, _now(), job_id),
            )

    def finish(self, job_id: str, result: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET state=?, result=?, progress=?, updated_at=? WHERE id=?",
                (DONE, json.dumps(result), "done", _now(), job_id),
            )

    def fail(self, job_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET state=?, error=?, updated_at=? WHERE id=?",
                (FAILED, error[:2000], _now(), job_id),
            )


def run_in_thread(store: JobStore, job_id: str, work: Callable[[Callable[[str], None]], dict]) -> threading.Thread:
    """Run `work` off the request thread, recording whatever happens.

    A failure here is data, not a crash: the job is marked failed with the reason,
    because a job stuck at "running" forever is the worst outcome for someone
    watching a progress line.
    """

    def target() -> None:
        try:
            result = work(lambda message: store.progress(job_id, message))
            store.finish(job_id, result)
        except Exception as exc:  # noqa: BLE001
            store.fail(job_id, f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}")

    thread = threading.Thread(target=target, daemon=True, name=f"recut-{job_id}")
    thread.start()
    return thread
