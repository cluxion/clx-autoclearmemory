from __future__ import annotations

import errno
import fcntl
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# path -> (st_dev, st_ino). Same path with a replaced inode must re-init.
_initialized_db_paths: dict[str, tuple[int, int]] = {}

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
    hold_fd: int | None = None
    try:
        # Pin the path's inode before sqlite open so a mid-connect os.replace
        # cannot be cached as an initialized DB. No retry: mismatch fails closed.
        hold_fd, expected = _open_db_path_identity(db_path)
        conn = sqlite3.connect(db_path)
        try:
            _require_db_identity(db_path, expected, stage="post-open")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            path_key = str(db_path.resolve())
            if _initialized_db_paths.get(path_key) != expected:
                # concurrent fresh-home first-inits (parallel `forgetforge store`)
                # race the rollback→WAL switch and the DDL burst below with
                # immediate SQLITE_BUSY that busy_timeout can't wait out —
                # serialize across processes. WAL is set by SCHEMA's first
                # pragma and is persistent, so no per-connect pragma is needed.
                lock_fd = _open_init_lock(db_path.parent / ".init.lock")
                try:
                    conn.executescript(SCHEMA)
                    _ensure_fts(conn)
                    # graph columns (node_type etc.) are required by the retrieval queries,
                    # so every connection must have them — not just graph-command paths.
                    from forgetforge import graph

                    graph.ensure_graph_schema(conn)
                    # One-time data migration needs graph columns present and must
                    # stay under the same init lock as schema setup.
                    _migrate_session_intent_uuid_session_ids(conn)
                finally:
                    _close_init_lock(lock_fd)
                _require_db_identity(db_path, expected, stage="post-schema")
                _initialized_db_paths[path_key] = expected
            _secure_db_files(db_path)
        except Exception:
            conn.close()
            raise
    finally:
        if hold_fd is not None:
            os.close(hold_fd)
        os.umask(old_umask)
    return conn


def _db_file_identity(db_path: Path) -> tuple[int, int] | None:
    try:
        st = db_path.stat()
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _open_db_path_identity(db_path: Path) -> tuple[int, tuple[int, int]]:
    """Non-truncating open/create of the DB path; return (fd, (dev, ino))."""
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    fd = os.open(str(db_path), flags, _PRIVATE_FILE_MODE)
    try:
        st = os.fstat(fd)
        return fd, (st.st_dev, st.st_ino)
    except Exception:
        os.close(fd)
        raise


def _require_db_identity(db_path: Path, expected: tuple[int, int], *, stage: str) -> None:
    identity = _db_file_identity(db_path)
    if identity != expected:
        raise RuntimeError(
            f"db path identity changed during {stage}: expected {expected!r}, got {identity!r} ({db_path})"
        )


def _open_init_lock(lock_path: Path) -> int:
    """Open home/.init.lock without following/truncating a symlink target.

    Uses O_RDWR|O_CREAT plus O_NOFOLLOW/O_CLOEXEC when available, verifies a
    regular owner-private file via fstat, fchmod 0600, then exclusive flock.
    """
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(str(lock_path), flags, _PRIVATE_FILE_MODE)
    except OSError:
        # Symlink (or other non-regular) fail closed without touching target.
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.ELOOP, f"init lock is not a regular file: {lock_path}")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise OSError(errno.EPERM, f"init lock not owned by current user: {lock_path}")
        os.fchmod(fd, _PRIVATE_FILE_MODE)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except Exception:
        os.close(fd)
        raise


def _close_init_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _secure_home_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
    path.chmod(_PRIVATE_DIR_MODE)


def _secure_db_files(db_path: Path) -> None:
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if path.exists():
            path.chmod(_PRIVATE_FILE_MODE)


# Durable marker for the one-time session-intent UUID session_id backfill.
# Bumped only by that migration; later opens no-op when user_version >= this.
_SESSION_INTENT_SESSION_ID_USER_VERSION = 1
_SESSION_INTENT_ID_PREFIX = "session-intent-"
_SESSION_INTENT_ID_RE = re.compile(
    r"session-intent-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _migrate_session_intent_uuid_session_ids(conn: sqlite3.Connection) -> None:
    """Backfill session_id on legacy session-intent-<uuid> archive rows once.

    Selects only node_type='session', session_id IS NULL, and id exactly equal to
    session-intent-<full UUID>. Leaves lookalikes, non-session rows, and already-
    set session_id values untouched. Records PRAGMA user_version atomically so
    later opens are no-ops. Session nodes remain graph-recallable.
    """
    row = conn.execute("PRAGMA user_version").fetchone()
    version = int(row[0] if row is not None else 0)
    if version >= _SESSION_INTENT_SESSION_ID_USER_VERSION:
        return

    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-check under the write lock so concurrent first-inits stay idempotent.
        row = conn.execute("PRAGMA user_version").fetchone()
        version = int(row[0] if row is not None else 0)
        if version >= _SESSION_INTENT_SESSION_ID_USER_VERSION:
            if began:
                conn.rollback()
            return
        candidates = conn.execute(
            """
            SELECT id FROM memories
            WHERE node_type = 'session'
              AND session_id IS NULL
              AND id LIKE ?
            """,
            (f"{_SESSION_INTENT_ID_PREFIX}%",),
        ).fetchall()
        updates = []
        for candidate in candidates:
            memory_id = str(candidate[0])
            match = _SESSION_INTENT_ID_RE.fullmatch(memory_id)
            if match is not None:
                updates.append((match.group(1), memory_id))
        conn.executemany(
            "UPDATE memories SET session_id = ? WHERE id = ? AND session_id IS NULL",
            updates,
        )
        conn.execute(f"PRAGMA user_version = {_SESSION_INTENT_SESSION_ID_USER_VERSION}")
        if began:
            conn.commit()
    except Exception:
        if began:
            conn.rollback()
        raise


def _ensure_fts(conn: sqlite3.Connection) -> dict[str, int]:
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
    # Backfill ANY row missing from the index, not just a fresh/empty index:
    # graph.ingest historically skipped _fts_upsert, leaving real DBs partially
    # un-indexed (memories > memories_fts) and content anchors blind to those
    # nodes. NOT IN materializes the fts ids once, so a synced DB pays one
    # cheap scan and backfills nothing.
    counts = {"backfilled": 0, "failed": 0}
    missing = conn.execute(
        """
        SELECT id, content FROM memories
        WHERE forget_requested = 0
          AND id NOT IN (SELECT memory_id FROM memories_fts WHERE memory_id IS NOT NULL)
        """
    ).fetchall()
    for row in missing:
        try:
            _fts_upsert(conn, str(row["id"]), str(row["content"]))
            counts["backfilled"] += 1
        except Exception:
            counts["failed"] += 1
    # Also drop reverse orphans: fts rows whose memory_id has no memories row.
    # Raw-SQL/manual deletes bypass _fts_delete and leave these, skewing bm25.
    conn.execute("DELETE FROM memories_fts WHERE memory_id NOT IN (SELECT id FROM memories)")
    conn.commit()
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
    node_type: str | None = None,
    expire_at: int | None = None,
    session_id: str | None = None,
) -> MemoryRow:
    ts = now_iso()
    # node_type/expire_at/session_id None = pre-flag behavior exactly:
    # 'memory'/NULL/NULL on insert, existing values untouched on update
    # (COALESCE against the row).
    conn.execute(
        """
        INSERT INTO memories (
            id, content, importance, frequency, is_procedural, keep_forever,
            created_at, updated_at, node_type, expire_at, session_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'memory'), ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            content = excluded.content,
            importance = excluded.importance,
            frequency = excluded.frequency,
            is_procedural = excluded.is_procedural,
            keep_forever = MAX(memories.keep_forever, excluded.keep_forever),
            forget_requested = 0,
            updated_at = excluded.updated_at,
            node_type = COALESCE(?, node_type),
            expire_at = COALESCE(?, expire_at),
            session_id = COALESCE(?, session_id)
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
            node_type,
            expire_at,
            session_id,
            node_type,
            expire_at,
            session_id,
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
                WHERE memories_fts MATCH ? AND m.forget_requested = 0 AND m.node_type = 'memory'
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
            WHERE forget_requested = 0 AND node_type = 'memory' AND content LIKE ?
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
            WHERE memories_fts MATCH ? AND m.forget_requested = 0 AND m.node_type = 'memory'
            ORDER BY bm25(memories_fts)
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [_row_to_memory(row) for row in rows]


def list_memories(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    node_type: str | None = None,
) -> list[MemoryRow]:
    """List active memories. Optional node_type is applied at SQL level.

    Callers that omit node_type keep the mixed-type listing (pruner/status/etc.).
    Contradiction fallback passes node_type='memory' so non-memory graph nodes
    are never loaded into the candidate pool.
    """
    if node_type is None:
        cur = conn.execute(
            """
            SELECT * FROM memories
            WHERE forget_requested = 0
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
    else:
        cur = conn.execute(
            """
            SELECT * FROM memories
            WHERE forget_requested = 0 AND node_type = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (node_type, limit),
        )
    return [_row_to_memory(row) for row in cur.fetchall()]


def list_hot_memories(conn: sqlite3.Connection, *, limit: int = 10) -> list[MemoryRow]:
    cur = conn.execute(
        """
        SELECT * FROM memories
        WHERE forget_requested = 0 AND tier = 'hot' AND node_type = 'memory'
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


# (new_tier, id, expected_tier, retrieval_count, importance, frequency,
#  is_procedural, keep_forever, forget_requested, last_recall_at, updated_at, content)
MemoryTierUpdate = tuple[str, str, str, float, float, float, bool | int, bool | int, bool | int, str | None, str, str]


def update_memory_tiers(
    conn: sqlite3.Connection,
    updates: list[MemoryTierUpdate],
) -> list[str]:
    """CAS-apply tier transitions from a pre-archive snapshot.

    Each update is
    ``(new_tier, memory_id, expected_tier, expected_retrieval_count,
    expected_importance, expected_frequency, expected_is_procedural,
    expected_keep_forever, expected_forget_requested, expected_last_recall_at,
    expected_updated_at, expected_content)``.
    Only ``tier`` / ``updated_at`` are written; every expected field is
    compared so pin/score/content races CAS-miss. Returns applied ids.
    """
    if not updates:
        return []
    stamp = now_iso()
    applied: list[str] = []
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for (
            new_tier,
            memory_id,
            expected_tier,
            expected_retrieval_count,
            expected_importance,
            expected_frequency,
            expected_is_procedural,
            expected_keep_forever,
            expected_forget_requested,
            expected_last_recall_at,
            expected_updated_at,
            expected_content,
        ) in updates:
            cur = conn.execute(
                """
                UPDATE memories
                SET tier = ?, updated_at = ?
                WHERE id = ?
                  AND tier = ?
                  AND retrieval_count = ?
                  AND importance = ?
                  AND frequency = ?
                  AND is_procedural = ?
                  AND keep_forever = ?
                  AND forget_requested = ?
                  AND last_recall_at IS ?
                  AND updated_at = ?
                  AND content = ?
                """,
                (
                    new_tier,
                    stamp,
                    memory_id,
                    expected_tier,
                    expected_retrieval_count,
                    expected_importance,
                    expected_frequency,
                    expected_is_procedural,
                    expected_keep_forever,
                    expected_forget_requested,
                    expected_last_recall_at,
                    expected_updated_at,
                    expected_content,
                ),
            )
            if cur.rowcount > 0:
                applied.append(memory_id)
        if began:
            conn.commit()
    except Exception:
        if began:
            conn.rollback()
        raise
    return applied


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
    "MemoryTierUpdate",
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
