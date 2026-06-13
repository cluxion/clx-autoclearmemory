from pathlib import Path

from forgetforge import db, store


def test_store_and_recall_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    stored = store.store_memory(
        conn,
        memory_id="rust-arch",
        content="Cluxion plugins use Rust for hot path scoring and queues.",
        importance=0.8,
    )
    assert stored["memory_id"] == "rust-arch"
    payload = store.recall_with_feedback(conn, "Rust")
    assert payload["count"] == 1
    assert payload["results"][0]["memory_id"] == "rust-arch"
    row = db.get_memory(conn, "rust-arch")
    assert row is not None
    assert row.retrieval_count == 0.45
    conn.close()


def test_recall_empty_message(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    payload = store.recall_with_feedback(conn, "nonexistent-topic")
    assert payload["count"] == 0
    assert payload["message"] == "no_memories_matched"
    assert "forgetforge store" in payload["hint"]
    conn.close()
