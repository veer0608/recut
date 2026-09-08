"""A queue of drafts waiting for a human to look at them.

recut measures its own unsupported-claim rate and the number is not zero. That
is survivable only because a person reads the output before it goes out, so the
path from "generated" to "published" runs through here rather than around it.

Two things make this a review rather than a list:

- Each draft carries its provenance, so a reviewer sees which span of the source
  stands behind each sentence instead of judging prose on its own.
- Each draft carries the source **as it was when the draft was written**. A URL
  can change under you, and a review against today's version of a page is not a
  review of what was actually generated.

Nothing here posts anything. `posted` is a human saying they did it.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PENDING, APPROVED, REJECTED, POSTED = "pending", "approved", "rejected", "posted"
STATES = (PENDING, APPROVED, REJECTED, POSTED)

# Approving something you rejected is a real change of mind and allowed. Marking
# something posted that nobody approved is not: it would let the review step be
# skipped by the one action that means it went public.
TRANSITIONS: dict[str, set[str]] = {
    PENDING: {APPROVED, REJECTED},
    APPROVED: {POSTED, REJECTED, PENDING},
    REJECTED: {PENDING, APPROVED},
    POSTED: set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id           TEXT PRIMARY KEY,
    job_id       TEXT,
    target       TEXT NOT NULL,
    state        TEXT NOT NULL,
    body         TEXT NOT NULL,
    source_title TEXT,
    source_ref   TEXT,
    source_raw   TEXT,
    sentences    TEXT,
    warnings     TEXT,
    files        TEXT,
    coverage     REAL,
    clean        INTEGER,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    note         TEXT
);
CREATE INDEX IF NOT EXISTS drafts_state ON drafts (state, created_at DESC);
"""


class TransitionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReviewQueue:
    def __init__(self, path: str | Path = "recut.db") -> None:
        self.path = str(path)
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn) -> None:
        """Columns added after a database already existed.

        CREATE TABLE IF NOT EXISTS does nothing to a table that is already
        there, so a queue created before `files` existed would keep working and
        silently drop every emitted project. Cheap to check, and the alternative
        is a draft that cannot be built for a reason nobody can see.
        """
        have = {row["name"] for row in conn.execute("PRAGMA table_info(drafts)")}
        if "files" not in have:
            conn.execute("ALTER TABLE drafts ADD COLUMN files TEXT")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # ----------------------------------------------------------------- writes

    def add(self, job_id: str, source: dict, artifact: dict) -> str:
        draft_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO drafts (id, job_id, target, state, body, source_title,"
                " source_ref, source_raw, sentences, warnings, files, coverage,"
                " clean, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    job_id,
                    artifact["target"],
                    PENDING,
                    artifact["body"],
                    source.get("title"),
                    source.get("ref"),
                    # Kept per draft on purpose: the review has to be against the
                    # source that produced it, not whatever the URL says today.
                    source.get("raw"),
                    json.dumps(artifact.get("sentences") or []),
                    json.dumps(artifact.get("warnings") or []),
                    # A target that emits files is not reviewable as prose
                    # alone, and rendering one before a human has looked costs
                    # minutes of compute on a draft that may be rejected.
                    json.dumps(artifact.get("files") or {}),
                    artifact.get("coverage"),
                    1 if artifact.get("clean") else 0,
                    _now(),
                    _now(),
                ),
            )
        return draft_id

    def set_state(self, draft_id: str, state: str, note: str | None = None) -> dict:
        if state not in STATES:
            raise TransitionError(f"unknown state {state!r}")
        draft = self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        current = draft["state"]
        if state != current and state not in TRANSITIONS[current]:
            raise TransitionError(
                f"cannot go from {current} to {state}"
                + (
                    ". Approve it first: marking something posted that nobody "
                    "approved would skip the review entirely."
                    if state == POSTED
                    else ""
                )
            )
        with self._connect() as conn:
            conn.execute(
                "UPDATE drafts SET state=?, note=COALESCE(?, note), updated_at=? WHERE id=?",
                (state, note, _now(), draft_id),
            )
        return self.get(draft_id)

    # ------------------------------------------------------------------ reads

    def get(self, draft_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return self._hydrate(row) if row else None

    def list(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = (
            "SELECT id, job_id, target, state, body, source_title, source_ref,"
            " coverage, clean, created_at, updated_at, note FROM drafts"
        )
        params: tuple = ()
        if state:
            query += " WHERE state=?"
            params = (state,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(query, (*params, limit)).fetchall()
        # Deliberately without sentences and source_raw: the list view does not
        # need them and they are the two largest columns by a wide margin.
        return [dict(r) | {"clean": bool(r["clean"])} for r in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT state, COUNT(*) c FROM drafts GROUP BY state").fetchall()
        return {state: 0 for state in STATES} | {r["state"]: r["c"] for r in rows}

    def _hydrate(self, row: sqlite3.Row) -> dict[str, Any]:
        draft = dict(row)
        draft["sentences"] = json.loads(draft["sentences"] or "[]")
        draft["warnings"] = json.loads(draft["warnings"] or "[]")
        # `.get` because a row written before the column existed has no key at
        # all, which is different from having an empty one.
        draft["files"] = json.loads(draft.get("files") or "{}")
        draft["clean"] = bool(draft["clean"])
        return draft
