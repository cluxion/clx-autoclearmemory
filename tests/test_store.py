import time
from pathlib import Path

import pytest

from forgetforge import db, graph, store


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
        content="Release 0.3.16 must stay visible immediately after storing.",
        importance=0.95,
    )
    assert stored["tier"] == "hot"
    assert stored["action"] == "inject_to_prompt"
    assert stored["retention"] == 1.0
    assert db.get_memory(conn, "launch").tier == "hot"
    conn.close()


def test_session_node_type_excluded_from_recall_and_hot_but_graph_reachable(tmp_path: Path, monkeypatch):
    # session archives must not crowd recall slots or hijack the hot tier,
    # yet stay reachable via graph anchors and carry a TTL
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(
        conn,
        memory_id="session-intent-abc",
        content="session archive zebraintent details",
        importance=0.9,  # born-hot importance: exclusion must survive the hot promotion
        node_type="session",
        expire_days=1,
    )
    payload = store.recall_with_feedback(conn, "zebraintent")
    assert payload["count"] == 0
    assert db.list_hot_memories(conn, limit=20) == []
    row = conn.execute("SELECT node_type, expire_at FROM memories WHERE id = 'session-intent-abc'").fetchone()
    assert row["node_type"] == "session"
    assert row["expire_at"] is not None and row["expire_at"] > time.time()
    hits = graph.graph_recall(conn, anchor_tags="zebraintent")
    assert [n["id"] for n in hits] == ["session-intent-abc"]
    conn.close()


def test_plain_store_calls_behave_exactly_as_before(tmp_path: Path, monkeypatch):
    # backward compat: no flags -> node_type 'memory', no expiry; and a plain
    # re-store never resets an existing row's node_type/expire_at
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="plain", content="plain fact")
    row = conn.execute("SELECT node_type, expire_at FROM memories WHERE id = 'plain'").fetchone()
    assert row["node_type"] == "memory" and row["expire_at"] is None

    store.store_memory(conn, memory_id="sess", content="v1", node_type="session", expire_days=2)
    before = conn.execute("SELECT node_type, expire_at FROM memories WHERE id = 'sess'").fetchone()
    store.store_memory(conn, memory_id="sess", content="v2")  # legacy call shape
    after = conn.execute("SELECT node_type, expire_at FROM memories WHERE id = 'sess'").fetchone()
    assert after["node_type"] == "session"
    assert after["expire_at"] == before["expire_at"]
    conn.close()


def test_store_rejects_invalid_node_type_and_negative_expiry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    with pytest.raises(ValueError, match="invalid node_type"):
        store.store_memory(conn, memory_id="x", content="c", node_type="bogus")
    with pytest.raises(ValueError, match="expire_days"):
        store.store_memory(conn, memory_id="x", content="c", expire_days=-1)
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
