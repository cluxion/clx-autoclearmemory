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
