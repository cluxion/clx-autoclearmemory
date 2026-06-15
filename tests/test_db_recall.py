from pathlib import Path

from forgetforge import db, recall


def test_recall_records_retrieval(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    row = db.upsert_memory(conn, memory_id="docker-setup", content="User prefers docker compose v2")
    assert row.retrieval_count == 0.0
    results = recall.recall_query(conn, "docker")
    assert len(results) == 1
    updated = db.get_memory(conn, "docker-setup")
    assert updated is not None
    assert updated.retrieval_count == 0.45
    assert updated.tier == "hot"
    conn.close()


def test_keep_forever_tag(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    db.upsert_memory(conn, memory_id="arch", content="Rust-first architecture")
    db.mark_keep_forever(conn, "arch")
    row = db.get_memory(conn, "arch")
    assert row is not None
    assert row.keep_forever is True
    conn.close()


def test_connect_accepts_str_path(tmp_path: Path, monkeypatch):
    # Regression: a live run passed str and crashed on Path-only handling.
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(str(tmp_path / "db.sqlite"))
    db.upsert_memory(conn, memory_id="s", content="str path works")
    conn.commit()
    assert db.get_memory(conn, "s") is not None
    conn.close()


def test_busy_timeout_is_set(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == 5000
    conn.close()
