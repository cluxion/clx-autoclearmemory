from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_initialized_db_paths: set[str] = set()

_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'warm_episodic',
    importance REAL NOT NULL DEFAULT 0.5,
    frequency REAL NOT NULL DEFAULT 0.0,
    retrieval_count REAL NOT NULL DEFAULT 0.0,
    is_procedural INTEGER NOT NULL DEFAULT 0,
    keep_forever INTEGER NOT NULL DEFAULT 0,
    forget_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_recall_at TEXT
);

CREATE TABLE IF NOT EXISTS retrieval_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    layer TEXT NOT NULL,
    boost REAL NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (memory_id) REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_retrieval_memory ON retrieval_events(memory_id);
"""


@dataclass(frozen=True)
class MemoryRow:
    id: str
    content: str
    tier: str
    importance: float
    frequency: float
    retrieval_count: float
    is_procedural: bool
    keep_forever: bool
    forget_requested: bool
    created_at: str
    updated_at: str
    last_recall_at: str | None


def connect(db_path: Path | str) -> sqlite3.Connection:
    # str paths crashed here in live use (same footgun as the prep guard
    # daemon); accept both like the rest of the public surface.
    db_path = Path(db_path)
    _secure_home_dir(db_path.parent)
    old_umask = os.umask(0o077)
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.row_factory = sqlite3.Row
            path_key = str(db_path.resolve())
            if path_key not in _initialized_db_paths:
                conn.executescript(SCHEMA)
                _ensure_fts(conn)
                _initialized_db_paths.add(path_key)
            _secure_db_files(db_path)
        except Exception:
            conn.close()
            raise
    finally:
        os.umask(old_umask)
    return conn


def _secure_home_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
    path.chmod(_PRIVATE_DIR_MODE)


def _secure_db_files(db_path: Path) -> None:
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if path.exists():
            path.chmod(_PRIVATE_FILE_MODE)


def _ensure_fts(conn: sqlite3.Connection) -> dict[str, int]:
    had_fts = (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts' LIMIT 1"
        ).fetchone()
        is not None
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            memory_id UNINDEXED,
            content,
            tokenize='porter'
        )
        """
    )
    conn.commit()
    needs_backfill_probe = not had_fts
    if not needs_backfill_probe:
        has_memory = conn.execute("SELECT 1 FROM memories WHERE forget_requested = 0 LIMIT 1").fetchone()
        if has_memory and conn.execute("SELECT 1 FROM memories_fts LIMIT 1").fetchone() is None:
            needs_backfill_probe = True
    counts = {"backfilled": 0, "failed": 0}
    if needs_backfill_probe:
        existing = conn.execute("SELECT COUNT(*) AS c FROM memories_fts").fetchone()
        memory_count = conn.execute("SELECT COUNT(*) AS c FROM memories WHERE forget_requested = 0").fetchone()
        if existing and memory_count and int(existing["c"]) == 0 and int(memory_count["c"]) > 0:
            for row in conn.execute("SELECT id, content FROM memories WHERE forget_requested = 0"):
                try:
                    _fts_upsert(conn, str(row["id"]), str(row["content"]))
                    counts["backfilled"] += 1
                except Exception:
                    counts["failed"] += 1
    return counts


def _fts_upsert(conn: sqlite3.Connection, memory_id: str, content: str) -> None:
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
        conn.execute("INSERT INTO memories_fts (memory_id, content) VALUES (?, ?)", (memory_id, content))
    except Exception:
        if owns_transaction:
            conn.rollback()
        raise
    if owns_transaction:
        conn.commit()


def _fts_delete(conn: sqlite3.Connection, memory_id: str) -> None:
    conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def upsert_memory(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    content: str,
    importance: float = 0.5,
    frequency: float = 0.0,
    is_procedural: bool = False,
    keep_forever: bool = False,
) -> MemoryRow:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO memories (
            id, content, importance, frequency, is_procedural, keep_forever,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            content = excluded.content,
            importance = excluded.importance,
            frequency = excluded.frequency,
            is_procedural = excluded.is_procedural,
            keep_forever = excluded.keep_forever,
            updated_at = excluded.updated_at
        """,
        (
            memory_id,
            content,
            importance,
            frequency,
            int(is_procedural),
            int(keep_forever),
            ts,
            ts,
        ),
    )
    _fts_upsert(conn, memory_id, content)
    conn.commit()
    row = get_memory(conn, memory_id)
    if row is None:
        raise RuntimeError(f"failed to upsert memory: {memory_id}")
    return row


def get_memory(conn: sqlite3.Connection, memory_id: str) -> MemoryRow | None:
    cur = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_memory(row)


def search_memories(conn: sqlite3.Connection, query: str, *, limit: int = 20) -> list[MemoryRow]:
    q = query.strip()
    if not q:
        return []
    fts_query = " ".join(f'"{part}"' for part in q.split() if part)
    rows: list[sqlite3.Row] = []
    if fts_query:
        try:
            cur = conn.execute(
                """
                SELECT m.*
                FROM memories_fts f
                JOIN memories m ON m.id = f.memory_id
                WHERE memories_fts MATCH ? AND m.forget_requested = 0
                ORDER BY bm25(memories_fts)
                LIMIT ?
                """,
                (fts_query, limit),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            rows = []
    if not rows:
        pattern = f"%{q}%"
        cur = conn.execute(
            """
            SELECT * FROM memories
            WHERE forget_requested = 0 AND content LIKE ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (pattern, limit),
        )
        rows = cur.fetchall()
    return [_row_to_memory(row) for row in rows]


def search_candidate_memories(conn: sqlite3.Connection, terms: set[str], *, limit: int = 40) -> list[MemoryRow]:
    clean_terms = sorted({term for term in terms if term.replace("_", "").isalnum()})
    if not clean_terms:
        return []
    fts_query = " OR ".join(f'"{term}"' for term in clean_terms)
    try:
        rows = conn.execute(
            """
            SELECT m.*
            FROM memories_fts f
            JOIN memories m ON m.id = f.memory_id
            WHERE memories_fts MATCH ? AND m.forget_requested = 0
            ORDER BY bm25(memories_fts)
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_memory(row) for row in rows]


def list_memories(conn: sqlite3.Connection, *, limit: int = 100) -> list[MemoryRow]:
    cur = conn.execute(
        """
        SELECT * FROM memories
        WHERE forget_requested = 0
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [_row_to_memory(row) for row in cur.fetchall()]


def list_hot_memories(conn: sqlite3.Connection, *, limit: int = 10) -> list[MemoryRow]:
    cur = conn.execute(
        """
        SELECT * FROM memories
        WHERE forget_requested = 0 AND tier = 'hot'
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [_row_to_memory(row) for row in cur.fetchall()]


def update_memory_state(
    conn: sqlite3.Connection,
    *,
    memory_id: str,
    tier: str,
    retrieval_count: float,
    last_recall_at: str | None = None,
    importance: float | None = None,
    frequency: float | None = None,
    commit: bool = True,
) -> None:
    sets = ["tier = ?", "retrieval_count = ?", "updated_at = ?", "last_recall_at = COALESCE(?, last_recall_at)"]
    params: list[Any] = [tier, retrieval_count, now_iso(), last_recall_at]
    if importance is not None:
        sets.append("importance = ?")
        params.append(importance)
    if frequency is not None:
        sets.append("frequency = ?")
        params.append(frequency)
    params.append(memory_id)
    conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params)
    if commit:
        conn.commit()


def update_memory_tiers(
    conn: sqlite3.Connection,
    updates: list[tuple[str, float, str]],
) -> int:
    """Apply (tier, retrieval_count, memory_id) updates in one transaction.

    Per-row commits fsync once per memory; a pruner run demoting hundreds of
    rows spends most of its wall-clock there, so batch with a single commit.
    """
    if not updates:
        return 0
    stamp = now_iso()
    conn.executemany(
        "UPDATE memories SET tier = ?, retrieval_count = ?, updated_at = ? WHERE id = ?",
        [(tier, retrieval_count, stamp, memory_id) for tier, retrieval_count, memory_id in updates],
    )
    conn.commit()
    return len(updates)


def bump_recall_stats(
    conn: sqlite3.Connection,
    memory_id: str,
    layer: str,
    *,
    row: MemoryRow | None = None,
    commit: bool = True,
) -> None:
    if row is None:
        row = get_memory(conn, memory_id)
    if row is None:
        return
    importance_delta = {"explicit": 0.03, "implicit": 0.02, "reflection": 0.01}.get(layer, 0.01)
    frequency_delta = 0.05
    update_memory_state(
        conn,
        memory_id=memory_id,
        tier=row.tier,
        retrieval_count=row.retrieval_count,
        importance=min(1.0, row.importance + importance_delta),
        frequency=min(1.0, row.frequency + frequency_delta),
        commit=commit,
    )


def mark_keep_forever(conn: sqlite3.Connection, memory_id: str) -> bool:
    cur = conn.execute(
        "UPDATE memories SET keep_forever = 1, tier = 'warm_semantic', updated_at = ? WHERE id = ?",
        (now_iso(), memory_id),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_forget(conn: sqlite3.Connection, memory_id: str, *, force: bool = False) -> dict[str, Any]:
    row = get_memory(conn, memory_id)
    if row is None:
        return {"ok": False, "reason": "memory not found", "memory_id": memory_id}
    if row.keep_forever and not force:
        return {"ok": False, "reason": "kept memory cannot be forgotten", "memory_id": memory_id}
    conn.execute(
        "UPDATE memories SET forget_requested = 1, tier = 'cold', updated_at = ? WHERE id = ?",
        (now_iso(), memory_id),
    )
    _fts_delete(conn, memory_id)
    conn.commit()
    return {"ok": True, "memory_id": memory_id}


def _restore_tier_for_unforget(row: MemoryRow) -> str:
    if row.keep_forever:
        return "warm_semantic"
    from forgetforge import recall, rust_bridge

    days = recall.days_since(row.last_recall_at)
    decision = rust_bridge.decide_tier(
        days_since_recall=days,
        retrieval_count=row.retrieval_count,
        importance=row.importance,
        frequency=row.frequency,
        is_procedural=row.is_procedural,
        keep_forever=row.keep_forever,
    )
    return str(decision["tier"])


def unforget(conn: sqlite3.Connection, memory_id: str) -> dict[str, Any]:
    row = get_memory(conn, memory_id)
    if row is None:
        return {"ok": False, "reason": "memory not found", "memory_id": memory_id}
    if not row.forget_requested:
        return {"ok": False, "reason": "memory is not forgotten", "memory_id": memory_id}
    tier = _restore_tier_for_unforget(row)
    conn.execute(
        "UPDATE memories SET forget_requested = 0, tier = ?, updated_at = ? WHERE id = ?",
        (tier, now_iso(), memory_id),
    )
    _fts_upsert(conn, memory_id, row.content)
    conn.commit()
    return {"ok": True, "memory_id": memory_id, "tier": tier}


def list_forgotten_memories(conn: sqlite3.Connection, *, limit: int = 100) -> list[MemoryRow]:
    cur = conn.execute(
        """
        SELECT * FROM memories
        WHERE forget_requested = 1
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [_row_to_memory(row) for row in cur.fetchall()]


def prune_retrieval_events(
    conn: sqlite3.Connection,
    *,
    max_age_days: int = 90,
    max_per_memory: int = 100,
) -> dict[str, int]:
    """Bound audit-only retrieval_events growth (not used in scoring)."""
    if max_age_days > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).replace(microsecond=0).isoformat()
        cur = conn.execute("DELETE FROM retrieval_events WHERE created_at < ?", (cutoff,))
        deleted_by_age = cur.rowcount
    else:
        deleted_by_age = 0

    deleted_by_cap = 0
    if max_per_memory > 0:
        rows = conn.execute(
            """
            SELECT memory_id, COUNT(*) AS c
            FROM retrieval_events
            GROUP BY memory_id
            HAVING c > ?
            """,
            (max_per_memory,),
        ).fetchall()
        for row in rows:
            memory_id = str(row["memory_id"])
            excess = int(row["c"]) - max_per_memory
            ids = conn.execute(
                """
                SELECT id FROM retrieval_events
                WHERE memory_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (memory_id, excess),
            ).fetchall()
            if ids:
                placeholders = ",".join("?" for _ in ids)
                cur = conn.execute(
                    f"DELETE FROM retrieval_events WHERE id IN ({placeholders})",
                    [int(r["id"]) for r in ids],
                )
                deleted_by_cap += cur.rowcount
    conn.commit()
    return {"deleted_by_age": deleted_by_age, "deleted_by_cap": deleted_by_cap}


def memory_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    cur = conn.execute(
        """
        SELECT tier, COUNT(*) AS count
        FROM memories
        WHERE forget_requested = 0
        GROUP BY tier
        """
    )
    tiers = {str(row["tier"]): int(row["count"]) for row in cur.fetchall()}
    total = sum(tiers.values())
    events = conn.execute("SELECT COUNT(*) AS c FROM retrieval_events").fetchone()
    return {
        "total_memories": total,
        "tiers": tiers,
        "retrieval_events": int(events["c"]) if events else 0,
    }


def _row_to_memory(row: sqlite3.Row) -> MemoryRow:
    return MemoryRow(
        id=str(row["id"]),
        content=str(row["content"]),
        tier=str(row["tier"]),
        importance=float(row["importance"]),
        frequency=float(row["frequency"]),
        retrieval_count=float(row["retrieval_count"]),
        is_procedural=bool(row["is_procedural"]),
        keep_forever=bool(row["keep_forever"]),
        forget_requested=bool(row["forget_requested"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_recall_at=str(row["last_recall_at"]) if row["last_recall_at"] else None,
    )


__all__ = [
    "MemoryRow",
    "bump_recall_stats",
    "connect",
    "get_memory",
    "list_forgotten_memories",
    "list_hot_memories",
    "list_memories",
    "mark_forget",
    "mark_keep_forever",
    "memory_stats",
    "prune_retrieval_events",
    "search_candidate_memories",
    "search_memories",
    "unforget",
    "update_memory_state",
    "update_memory_tiers",
    "upsert_memory",
]
