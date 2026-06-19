from pathlib import Path

from forgetforge import db, hot_inject, recall, store


def test_hot_context_lists_hot_memories(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="hot-1", content="Hermes plugins use forgetforge for memory")
    recall.recall_query(conn, "forgetforge", layer="explicit")
    ctx = hot_inject.build_hot_context(conn)
    assert "hot-1" in ctx
    assert "ForgetForge Hot" in ctx
    conn.close()


def test_high_importance_memory_surfaces_without_recall(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(
        conn,
        memory_id="flight",
        content="User flight departs at 6am tomorrow from terminal two",
        importance=0.9,
    )
    ctx = hot_inject.build_hot_context(conn)
    assert "flight" in ctx
    conn.close()


def test_hermes_pre_llm_hook(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    from forgetforge.adapters import hermes

    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="x", content="alpha beta gamma delta")
    db.update_memory_state(conn, memory_id="x", tier="hot", retrieval_count=1.0)
    conn.close()
    payload = hermes._pre_llm_hot_inject()
    assert "context" in payload
    assert "x" in payload["context"]
