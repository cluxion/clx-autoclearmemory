from pathlib import Path

from forgetforge import contradiction, db, store


def test_detects_negation_contradiction(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="a", content="User always prefers docker compose for local development")
    hits = contradiction.detect_contradictions(conn, content="User never prefers docker compose for local development")
    assert hits
    assert hits[0].memory_id == "a"
    assert hits[0].reason.startswith("negation_pair:")
    conn.close()


def test_detects_substitution_contradiction(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(
        conn,
        memory_id="svc-net",
        content="Service api-gateway uses bridge networking for local docker compose",
    )
    hits = contradiction.detect_contradictions(
        conn,
        content="Service api-gateway uses host networking for local docker compose",
    )
    assert hits
    assert hits[0].memory_id == "svc-net"
    assert hits[0].reason == "conflicting_claim"
    conn.close()


def test_similar_memories_are_not_contradictions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(
        conn,
        memory_id="rust-a",
        content="Project uses Rust for all hot path scoring engines and queues",
    )
    hits = contradiction.detect_contradictions(
        conn,
        content="Project uses Rust for all hot path scoring engines and batch workers",
    )
    assert hits == []
    conn.close()


def test_unrelated_negations_are_not_contradictions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="docker", content="User never uses Docker for production deployments")
    hits = contradiction.detect_contradictions(
        conn,
        content="User always prefers Python for scripting automation tasks",
    )
    assert hits == []
    conn.close()


def test_store_returns_advisory_warnings(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="old", content="Project uses Rust for all hot path scoring engines")
    stored = store.store_memory(conn, memory_id="new", content="Project never uses Rust for hot path scoring engines")
    assert "contradiction_warnings" in stored
    assert stored["contradiction_warnings_advisory"] is True
    assert stored["contradiction_warnings"][0]["advisory"] is True
    conn.close()


def test_detect_contradictions_prefilters_with_fts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    for index in range(500):
        store.store_memory(
            conn,
            memory_id=f"noise-{index}",
            content=f"Project component {index} uses postgres shard {index} for background queue processing",
            check_contradictions=False,
        )
    store.store_memory(
        conn,
        memory_id="target",
        content="User always prefers docker compose for local development",
        check_contradictions=False,
    )

    calls = 0
    original_tokens = contradiction._tokens

    def counting_tokens(text: str):
        nonlocal calls
        calls += 1
        return original_tokens(text)

    monkeypatch.setattr(contradiction, "_tokens", counting_tokens)
    hits = contradiction.detect_contradictions(
        conn,
        content="User never prefers docker compose for local development",
    )
    assert hits and hits[0].memory_id == "target"
    assert calls < 50
    conn.close()


def test_always_intensifier_is_not_contradiction(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="redis", content="Use redis cache for sessions and request throttling")
    hits = contradiction.detect_contradictions(
        conn,
        content="Always use redis cache for sessions and request throttling",
    )
    assert hits == []
    conn.close()


def test_fts_empty_fallback_excludes_session_nodes_from_contradictions(tmp_path: Path, monkeypatch):
    # When FTS memory candidates are empty, the SQL fallback must still filter
    # node_type='memory' so session archives never pollute contradiction hits.
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    session_id = "session-intent-contradiction-isolation"
    session_claim = "User always prefers docker compose for local development"
    probe = "User never prefers docker compose for local development"
    store.store_memory(
        conn,
        memory_id=session_id,
        content=session_claim,
        node_type="session",
        check_contradictions=False,
    )
    # FTS candidate search is memory-only; empty forces the fallback path.
    assert db.search_candidate_memories(conn, contradiction._tokens(probe), limit=40) == []

    hits = contradiction.detect_contradictions(conn, content=probe)
    assert hits == []
    assert all(h.memory_id != session_id for h in hits)

    stored = store.store_memory(conn, memory_id="docker-never", content=probe)
    warnings = stored.get("contradiction_warnings", [])
    assert "contradiction_warnings" not in stored or warnings == []
    assert all(w.get("memory_id") != session_id for w in warnings)

    # Ordinary memory-vs-memory detection must keep working alongside session rows.
    store.store_memory(
        conn,
        memory_id="mem-old",
        content="Project uses Rust for all hot path scoring engines",
        check_contradictions=False,
    )
    mem_hits = contradiction.detect_contradictions(
        conn,
        content="Project never uses Rust for hot path scoring engines",
    )
    assert mem_hits and mem_hits[0].memory_id == "mem-old"
    assert all(h.memory_id != session_id for h in mem_hits)
    mem_stored = store.store_memory(
        conn,
        memory_id="mem-new",
        content="Project never uses Rust for hot path scoring engines",
    )
    assert mem_stored["contradiction_warnings"][0]["memory_id"] == "mem-old"
    assert all(w["memory_id"] != session_id for w in mem_stored["contradiction_warnings"])
    conn.close()
