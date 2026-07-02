import sqlite3
from pathlib import Path

from forgetforge import db, store


def test_fts_finds_stored_memory(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(
        conn, memory_id="rust-queue", content="Cluxion preprocessing uses Rust cluxion-queue for dispatch"
    )
    matches = db.search_memories(conn, "dispatch")
    assert any(m.id == "rust-queue" for m in matches)
    conn.close()


def test_fts_backfill_continues_after_bad_row(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    conn.execute(
        """
        INSERT INTO memories (id, content, created_at, updated_at)
        VALUES ('bad', 'bad row', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO memories (id, content, created_at, updated_at)
        VALUES ('good', 'good searchable row', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.commit()

    original_upsert = db._fts_upsert

    def flaky_upsert(connection, memory_id: str, content: str):
        if memory_id == "bad":
            raise RuntimeError("bad fts row")
        return original_upsert(connection, memory_id, content)

    monkeypatch.setattr(db, "_fts_upsert", flaky_upsert)
    counts = db._ensure_fts(conn)
    assert counts == {"backfilled": 1, "failed": 1}
    assert conn.execute("SELECT memory_id FROM memories_fts").fetchall()[0]["memory_id"] == "good"
    conn.close()
