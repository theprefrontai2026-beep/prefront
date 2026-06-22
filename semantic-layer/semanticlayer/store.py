"""SQLite persistence for generated query templates + their approval status.

Templates are produced by the ``/design/semantic/build`` endpoint and reviewed
(approve/reject) in the Interfaces tab. Persisting them here means approvals
survive a page refresh or a container restart — mirroring how the skill-builder
persists rule approvals.

A build replaces the template set for its ``semantic_model_id`` (a fresh
generation needs fresh review), so statuses default to ``pending`` on build and
are updated by approve/reject.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_templates (
  template_id       TEXT PRIMARY KEY,
  semantic_model_id TEXT,
  datasource_id     TEXT,
  intent_id         TEXT,
  kind              TEXT,
  template_json     TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'pending',
  reviewer          TEXT,
  created_at        TEXT DEFAULT (datetime('now')),
  updated_at        TEXT DEFAULT (datetime('now'))
);

-- Candidate CRUD operations derived from a connected schema (suggest_intents),
-- with an LLM-written description, reviewed (approved) in the Data Connector tab.
CREATE TABLE IF NOT EXISTS functions (
  datasource_id  TEXT NOT NULL,
  name           TEXT NOT NULL,
  verb           TEXT,
  entity         TEXT,
  description    TEXT,
  status         TEXT NOT NULL DEFAULT 'pending',
  created_at     TEXT DEFAULT (datetime('now')),
  updated_at     TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (datasource_id, name)
);

-- The schema source for a datasource, kept so approving a function can rebuild
-- the catalog and regenerate its query template without re-uploading.
-- owner_column = the column read templates scope by (WHERE owner = :caller_<owner>).
CREATE TABLE IF NOT EXISTS datasources (
  datasource_id  TEXT PRIMARY KEY,
  ddl            TEXT,
  dsn            TEXT,
  owner_column   TEXT,
  updated_at     TEXT DEFAULT (datetime('now'))
);
"""


class Store:
    """Thin data-access layer over a SQLite file."""

    def __init__(self, path: str | Path = "semanticlayer.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        # Lightweight migration: add owner_column to an existing datasources table.
        try:
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(datasources)")}
            if "owner_column" not in cols:
                self._conn.execute("ALTER TABLE datasources ADD COLUMN owner_column TEXT")
        except Exception:  # noqa: BLE001
            pass
        self._conn.commit()

    @contextmanager
    def _tx(self):
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @staticmethod
    def _row_to_template(r: sqlite3.Row) -> dict[str, Any]:
        """The full template dict plus its review status + generation timestamps."""
        t = json.loads(r["template_json"])
        t["status"] = r["status"]
        t["reviewer"] = r["reviewer"]
        t["created_at"] = r["created_at"]   # set at generation (UTC)
        t["updated_at"] = r["updated_at"]
        return t

    def replace_templates(
        self, semantic_model_id: str, datasource_id: Optional[str], templates: list[dict]
    ) -> list[dict]:
        """Replace the persisted template set for a semantic model; return the rows."""
        with self._tx() as c:
            c.execute(
                "DELETE FROM query_templates WHERE semantic_model_id=?", (semantic_model_id,)
            )
            # template_id is the global primary key, so also clear any incoming
            # ids still held under a *different* semantic_model_id (e.g. a prior
            # build/import that used another model id) — otherwise the INSERT
            # below trips the UNIQUE constraint. Keeps replace idempotent.
            ids = [t["template_id"] for t in templates]
            if ids:
                placeholders = ",".join("?" * len(ids))
                c.execute(
                    f"DELETE FROM query_templates WHERE template_id IN ({placeholders})", ids
                )
            for t in templates:
                c.execute(
                    """INSERT INTO query_templates
                       (template_id, semantic_model_id, datasource_id, intent_id, kind,
                        template_json, status)
                       VALUES (?,?,?,?,?,?, 'pending')""",
                    (
                        t["template_id"], semantic_model_id, datasource_id,
                        t.get("intent_id"), t.get("kind"), json.dumps(t),
                    ),
                )
        return self.list_templates(semantic_model_id)

    def list_templates(self, semantic_model_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            if semantic_model_id:
                rows = self._conn.execute(
                    "SELECT * FROM query_templates WHERE semantic_model_id=? ORDER BY intent_id",
                    (semantic_model_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM query_templates ORDER BY updated_at DESC"
                ).fetchall()
        return [self._row_to_template(r) for r in rows]

    def set_status(self, template_id: str, status: str, reviewer: str = "ui_reviewer") -> dict:
        with self._tx() as c:
            cur = c.execute(
                """UPDATE query_templates
                   SET status=?, reviewer=?, updated_at=datetime('now')
                   WHERE template_id=?""",
                (status, reviewer, template_id),
            )
            if cur.rowcount == 0:
                raise KeyError(template_id)
            row = c.execute(
                "SELECT * FROM query_templates WHERE template_id=?", (template_id,)
            ).fetchone()
        return self._row_to_template(row)

    # --- derived CRUD functions (Data Connector review) ----------------------

    def upsert_functions(self, datasource_id: str, functions: list[dict]) -> None:
        """Insert new functions (status pending); refresh the description but
        PRESERVE an existing approval status, so recomputing doesn't lose approvals."""
        with self._tx() as c:
            for f in functions:
                c.execute(
                    """INSERT INTO functions (datasource_id, name, verb, entity, description)
                       VALUES (?,?,?,?,?)
                       ON CONFLICT(datasource_id, name) DO UPDATE SET
                         verb=excluded.verb, entity=excluded.entity,
                         description=excluded.description, updated_at=datetime('now')""",
                    (datasource_id, f["name"], f.get("verb"), f.get("entity"),
                     f.get("description")),
                )

    def list_functions(self, datasource_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, verb, entity, description, status FROM functions "
                "WHERE datasource_id=? ORDER BY entity, verb", (datasource_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def set_function(self, datasource_id: str, name: str, status: str) -> None:
        with self._tx() as c:
            cur = c.execute(
                "UPDATE functions SET status=?, updated_at=datetime('now') "
                "WHERE datasource_id=? AND name=?", (status, datasource_id, name))
            if cur.rowcount == 0:
                raise KeyError(name)

    def set_all_functions(self, datasource_id: str, status: str) -> int:
        """Approve-all / reset: set every function for the datasource to `status`."""
        with self._tx() as c:
            cur = c.execute(
                "UPDATE functions SET status=?, updated_at=datetime('now') WHERE datasource_id=?",
                (status, datasource_id))
        return cur.rowcount

    def upsert_datasource(self, datasource_id: str, ddl: Optional[str], dsn: Optional[str],
                          owner_column: Optional[str] = None) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO datasources (datasource_id, ddl, dsn, owner_column) VALUES (?,?,?,?)
                   ON CONFLICT(datasource_id) DO UPDATE SET
                     ddl=excluded.ddl, dsn=excluded.dsn, owner_column=excluded.owner_column,
                     updated_at=datetime('now')""",
                (datasource_id, ddl, dsn, owner_column))

    def get_datasource(self, datasource_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT datasource_id, ddl, dsn, owner_column FROM datasources WHERE datasource_id=?",
                (datasource_id,)).fetchone()
        return dict(row) if row else None
