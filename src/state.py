"""SQLite-backed state for pending birthday drafts.

Stores each draft so that:
  - regenerate ("🔄") can feed prior drafts back into the LLM for variety
  - approve ("✅") knows what to post and where
  - bot restarts don't lose pending approvals

Schema is intentionally minimal — this is one user with maybe ten birthdays a
year. We don't need migrations.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_name TEXT NOT NULL,
    person_handle TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    current_draft TEXT NOT NULL,
    prior_drafts_json TEXT NOT NULL DEFAULT '[]',
    owner_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | skipped
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive column migrations for existing DBs (pre-Role schema)."""
    cur = conn.execute("PRAGMA table_info(drafts)")
    existing = {row[1] for row in cur.fetchall()}
    if "role" not in existing:
        conn.execute("ALTER TABLE drafts ADD COLUMN role TEXT NOT NULL DEFAULT ''")
        logger.info("Migrated drafts table: added 'role' column")


@dataclass
class Draft:
    id: int
    person_name: str
    person_handle: str
    department: str
    role: str
    notes: str
    current_draft: str
    prior_drafts: List[str]
    owner_message_id: Optional[int]
    status: str
    created_at: str
    resolved_at: Optional[str]


class StateStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            _migrate(c)
        logger.info("State DB ready at %s", db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _row_to_draft(row: sqlite3.Row) -> Draft:
        return Draft(
            id=row["id"],
            person_name=row["person_name"],
            person_handle=row["person_handle"],
            department=row["department"],
            role=row["role"] if "role" in row.keys() else "",
            notes=row["notes"],
            current_draft=row["current_draft"],
            prior_drafts=json.loads(row["prior_drafts_json"] or "[]"),
            owner_message_id=row["owner_message_id"],
            status=row["status"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    def create_draft(
        self,
        *,
        person_name: str,
        person_handle: str,
        department: str,
        role: str,
        notes: str,
        current_draft: str,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO drafts
                   (person_name, person_handle, department, role, notes,
                    current_draft, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (person_name, person_handle, department, role, notes,
                 current_draft, self._now()),
            )
            return cur.lastrowid  # type: ignore

    def attach_owner_message_id(self, draft_id: int, message_id: int) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE drafts SET owner_message_id = ? WHERE id = ?",
                (message_id, draft_id),
            )

    def get(self, draft_id: int) -> Optional[Draft]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            return self._row_to_draft(row) if row else None

    def replace_draft_text(self, draft_id: int, new_text: str) -> None:
        """Move current_draft into prior_drafts, then store new text as current."""
        with self._conn() as c:
            row = c.execute(
                "SELECT current_draft, prior_drafts_json FROM drafts WHERE id = ?",
                (draft_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"draft {draft_id} not found")
            prior = json.loads(row["prior_drafts_json"] or "[]")
            prior.append(row["current_draft"])
            c.execute(
                """UPDATE drafts SET current_draft = ?, prior_drafts_json = ?
                   WHERE id = ?""",
                (new_text, json.dumps(prior, ensure_ascii=False), draft_id),
            )

    def mark_resolved(self, draft_id: int, status: str) -> None:
        assert status in ("approved", "skipped")
        with self._conn() as c:
            c.execute(
                "UPDATE drafts SET status = ?, resolved_at = ? WHERE id = ?",
                (status, self._now(), draft_id),
            )

    def pending(self) -> list[Draft]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM drafts WHERE status = 'pending' ORDER BY id"
            ).fetchall()
            return [self._row_to_draft(r) for r in rows]
