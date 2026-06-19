"""Pruner batch demotion: one sqlite transaction, one archive pass per run."""

from __future__ import annotations

import json
from pathlib import Path

from forgetforge import archive, db, pruner, recall, store
from forgetforge.config import load_config


def _isolated_conn(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    assert str(cfg.db_path).startswith(str(tmp_path))
    return db.connect(cfg.db_path), cfg


def test_pruner_batch_demotes_and_archives(tmp_path: Path, monkeypatch):
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    for i in range(5):
        store.store_memory(
            conn,
            memory_id=f"mem-{i}",
            content=f"memory body {i}\nsecond line",
            importance=0.1,
        )
    # New memories start cold (recall-centric design); simulate a prior warm
    # state so the pruner has transitions to apply.
    conn.execute("UPDATE memories SET tier = 'warm_episodic'")
    conn.commit()

    result = pruner.run_pruner(conn, config=cfg)

    assert result["ok"] is True
    assert sorted(result["demoted_to_cold"]) == [f"mem-{i}" for i in range(5)]
    for i in range(5):
        row = db.get_memory(conn, f"mem-{i}")
        assert row is not None and row.tier == "cold"
        assert (cfg.archive_dir / f"mem-{i}.txt").exists()
    jsonl_lines = (cfg.archive_dir / "cold_archive.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(jsonl_lines) == 5
    assert {json.loads(line)["memory_id"] for line in jsonl_lines} == {f"mem-{i}" for i in range(5)}
    parquet_files = list(cfg.archive_dir.glob("cold_*.parquet"))
    try:
        import pyarrow.parquet as pq
    except ImportError:
        assert parquet_files == []
    else:
        assert len(parquet_files) == 1
        table = pq.read_table(parquet_files[0])
        assert table.num_rows == 5
    conn.close()


def test_pruner_noop_writes_nothing(tmp_path: Path, monkeypatch):
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    store.store_memory(conn, memory_id="solo", content="already cold", importance=0.1)
    result = pruner.run_pruner(conn, config=cfg)
    assert result["demoted_to_cold"] == []
    assert not (cfg.archive_dir / "cold_archive.jsonl").exists()
    assert list(cfg.archive_dir.glob("*.parquet")) == []
    conn.close()


def test_write_cold_archive_single_still_works(tmp_path: Path, monkeypatch):
    _, cfg = _isolated_conn(tmp_path, monkeypatch)
    result = archive.write_cold_archive(cfg, memory_id="legacy", content="legacy body", retention=0.2, tier="cold")
    assert result["format"] in {"parquet", "jsonl"}
    assert (cfg.archive_dir / "legacy.txt").exists()
    assert (cfg.archive_dir / "cold_archive.jsonl").exists()


def test_write_cold_archive_batch_empty_is_noop(tmp_path: Path, monkeypatch):
    _, cfg = _isolated_conn(tmp_path, monkeypatch)
    result = archive.write_cold_archive_batch(cfg, [])
    assert result == {"format": "noop", "parquet": None, "jsonl": None, "count": 0}
    assert not cfg.archive_dir.exists() or not any(cfg.archive_dir.iterdir())


def test_pruner_bounds_retrieval_events(tmp_path: Path, monkeypatch):
    conn, cfg = _isolated_conn(tmp_path, monkeypatch)
    store.store_memory(conn, memory_id="evt", content="retrieval event pruning target memory")
    for _ in range(5):
        recall.recall_query(conn, "retrieval")
    old_ts = "2000-01-01T00:00:00+00:00"
    conn.execute("UPDATE retrieval_events SET created_at = ?", (old_ts,))
    conn.commit()
    assert db.memory_stats(conn)["retrieval_events"] == 5

    cfg = cfg.__class__(
        **{
            **cfg.__dict__,
            "retrieval_events_max_age_days": 30,
            "retrieval_events_max_per_memory": 2,
        }
    )
    result = pruner.run_pruner(conn, config=cfg)

    assert result["retrieval_events_gc"]["deleted_by_age"] >= 1
    assert db.memory_stats(conn)["retrieval_events"] <= 2
    row = db.get_memory(conn, "evt")
    assert row is not None
    assert row.retrieval_count > 0
    conn.close()


def test_update_memory_tiers_batch(tmp_path: Path, monkeypatch):
    conn, _ = _isolated_conn(tmp_path, monkeypatch)
    for i in range(3):
        store.store_memory(conn, memory_id=f"t-{i}", content=f"tier test {i}", importance=0.5)
    applied = db.update_memory_tiers(conn, [("hot", 2.0, "t-0"), ("warm_semantic", 1.0, "t-1")])
    assert applied == 2
    assert db.get_memory(conn, "t-0").tier == "hot"
    assert db.get_memory(conn, "t-1").tier == "warm_semantic"
    assert db.get_memory(conn, "t-2").tier != "hot"
    assert db.update_memory_tiers(conn, []) == 0
    conn.close()
