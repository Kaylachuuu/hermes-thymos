"""Where her moments are kept: one SQLite file.

Hermes gives every plugin a data folder that survives updates and removal of the plugin's
code: ``<hermes home>/plugin-data/thymos/``.  The file is ``thymos.db`` in it.

Every row records the visibility it was written under.  What a row may show is decided by
that, not by today's setting: a note she wrote when told it was hers stays hers.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import OPEN

FELT = "felt"          # she answered
ERROR = "error"        # the call failed, or the answer had no words in it
SKIPPED = "skipped"    # no moment was taken, and why

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS moments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    turn_id     TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    visibility  TEXT NOT NULL,
    status      TEXT NOT NULL,
    words       TEXT NOT NULL DEFAULT '',
    intensity   REAL,
    raw         TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    tokens      INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS moments_created ON moments (created_at);
"""


def default_path() -> Path:
    """``<hermes home>/plugin-data/thymos/thymos.db``.  Only works inside Hermes."""
    from plugins.plugin_storage import plugin_data_dir
    return plugin_data_dir("thymos") / "thymos.db"


class Store:
    """A connection is opened for each operation, so any thread may call any method."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._open() as db:
            db.executescript(_SCHEMA)
            db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    @contextlib.contextmanager
    def _open(self):
        """One connection: committed if the block finishes, rolled back if it raises, always closed
        (Windows keeps a file locked for as long as a connection to it is open)."""
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, *, status: str, visibility: str, words: str = "", intensity: Optional[float] = None,
            raw: str = "", session_id: str = "", turn_id: str = "", platform: str = "", provider: str = "",
            model: str = "", tokens: int = 0, duration_ms: int = 0, note: str = "",
            created_at: Optional[float] = None) -> int:
        with self._open() as db:
            cursor = db.execute(
                "INSERT INTO moments (created_at, session_id, turn_id, platform, visibility, status, words, intensity, "
                "raw, provider, model, tokens, duration_ms, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time() if created_at is None else created_at, session_id or "", turn_id or "", platform or "",
                 visibility, status, words or "", intensity, raw or "", provider or "", model or "",
                 int(tokens or 0), int(duration_ms or 0), note or ""))
            return int(cursor.lastrowid)

    def recent(self, limit: int = 20, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """The latest rows, oldest first.  Words and the raw reply are blanked on any row that was
        not written as open, so nothing that reads through this method can show them."""
        sql, params = "SELECT * FROM moments", []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._open() as db:
            rows = [dict(r) for r in db.execute(sql, params)]
        rows.reverse()
        for row in rows:
            row["readable"] = row["visibility"] == OPEN
            if not row["readable"]:
                row["words"], row["raw"] = "", ""
        return rows

    def summary(self) -> Dict[str, Any]:
        with self._open() as db:
            counts = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM moments GROUP BY status")}
            felt = db.execute(
                "SELECT COUNT(*) AS n, AVG(duration_ms) AS ms, AVG(intensity) AS intensity, "
                "SUM(intensity IS NULL) AS unnumbered, MAX(created_at) AS last FROM moments WHERE status = ?",
                (FELT,)).fetchone()
            models = [r["model"] for r in db.execute(
                "SELECT model, MAX(id) AS latest FROM moments WHERE status = ? AND model != '' "
                "GROUP BY model ORDER BY latest DESC", (FELT,))]
        return {
            "felt": int(counts.get(FELT, 0)), "errors": int(counts.get(ERROR, 0)), "skipped": int(counts.get(SKIPPED, 0)),
            "average_ms": float(felt["ms"]) if felt["ms"] is not None else None,
            "average_intensity": float(felt["intensity"]) if felt["intensity"] is not None else None,
            "without_number": int(felt["unnumbered"] or 0),
            "last_felt_at": float(felt["last"]) if felt["last"] is not None else None,
            "models": models,
        }
