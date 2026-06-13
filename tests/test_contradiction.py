from pathlib import Path

from forgetforge import contradiction, db, store


def test_detects_negation_contradiction(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="a", content="User always prefers docker compose for local development")
    hits = contradiction.detect_contradictions(conn, content="User never prefers docker compose for local development")
    assert hits
    assert hits[0].memory_id == "a"
    conn.close()


def test_store_returns_warnings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="old", content="Project uses Rust for all hot path scoring engines")
    stored = store.store_memory(conn, memory_id="new", content="Project never uses Rust for hot path scoring engines")
    assert "contradiction_warnings" in stored
    conn.close()
