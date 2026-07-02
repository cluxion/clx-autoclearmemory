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


def test_fresh_high_importance_memory_reports_born_hot_contract(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    stored = store.store_memory(
        conn,
        memory_id="launch",
        content="Release 0.3.15 must stay visible immediately after storing.",
        importance=0.95,
    )
    assert stored["tier"] == "hot"
    assert stored["action"] == "inject_to_prompt"
    assert stored["retention"] == 1.0
    assert db.get_memory(conn, "launch").tier == "hot"
    conn.close()


def test_recall_retention_is_user_normalized(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(
        conn,
        memory_id="frequent",
        content="Frequent memory for normalized retention output.",
        importance=1.0,
        frequency=1.0,
    )
    db.update_memory_state(conn, memory_id="frequent", tier="hot", retrieval_count=25.0)
    payload = store.recall_with_feedback(conn, "Frequent")
    retention = payload["results"][0]["retention"]
    assert 0.0 <= retention <= 1.0
    conn.close()
