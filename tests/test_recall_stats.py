from pathlib import Path

from forgetforge import db, recall, store


def test_recall_bumps_importance_and_frequency(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = db.connect(tmp_path / "db.sqlite")
    store.store_memory(conn, memory_id="stat", content="Frequency and importance bump on recall")
    recall.recall_query(conn, "importance", layer="explicit")
    row = db.get_memory(conn, "stat")
    assert row is not None
    assert row.importance > 0.5
    assert row.frequency > 0.0
    conn.close()
