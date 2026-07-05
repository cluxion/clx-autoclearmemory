from __future__ import annotations

import fcntl
import json
import os
import time
from typing import Any

from forgetforge import archive, db, graph, recall, rust_bridge
from forgetforge.config import ForgetForgeConfig, load_config


def run_pruner(conn, config: ForgetForgeConfig | None = None) -> dict[str, Any]:
    """Background pruner: demote low-retention memories to cold tier."""
    cfg = config or load_config()
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    demoted: list[str] = []
    promoted: list[str] = []
    rows = [row for row in db.list_memories(conn, limit=10_000) if not (row.keep_forever or row.forget_requested)]
    # One engine call for the whole table: the JSON boundary dominates
    # per-row invocations, so batching is where Rust actually wins.
    decisions = rust_bridge.decide_tier_batch(
        [
            {
                "days_since_recall": recall.days_since(row.last_recall_at),
                "retrieval_count": row.retrieval_count,
                "importance": row.importance,
                "frequency": row.frequency,
                "is_procedural": row.is_procedural,
                "keep_forever": row.keep_forever,
            }
            for row in rows
        ]
    )
    # Collect transitions first, then apply in bulk: one sqlite transaction
    # and one archive pass instead of per-row commit + parquet write.
    tier_updates: list[tuple[str, float, str]] = []
    archive_records: list[dict[str, Any]] = []
    for row, decision in zip(rows, decisions, strict=True):
        new_tier = str(decision["tier"])
        if new_tier == row.tier:
            continue
        tier_updates.append((new_tier, row.retrieval_count, row.id))
        if new_tier == "cold":
            demoted.append(row.id)
            archive_records.append(
                {
                    "memory_id": row.id,
                    "content": row.content,
                    "retention": decision["retention"],
                    "tier": new_tier,
                }
            )
        elif row.tier == "cold":
            promoted.append(row.id)
    db.update_memory_tiers(conn, tier_updates)
    archive.write_cold_archive_batch(cfg, archive_records)
    retrieval_gc = db.prune_retrieval_events(
        conn,
        max_age_days=cfg.retrieval_events_max_age_days,
        max_per_memory=cfg.retrieval_events_max_per_memory,
    )
    ttl_swept = graph.sweep_expired(conn)
    return {
        "ok": True,
        "demoted_to_cold": demoted,
        "promoted_from_cold": promoted,
        "interval_hours": cfg.pruner_interval_hours,
        "retrieval_events_gc": retrieval_gc,
        "ttl_swept": ttl_swept,
    }


def run_pruner_daemon(*, interval_hours: int | None = None, run_once: bool = False, max_cycles: int = 24) -> int:
    """Run pruner on an interval with a hard cycle cap, one JSON summary per cycle."""
    if max_cycles < 1:
        raise ValueError(f"max_cycles must be >= 1, got {max_cycles}")
    if interval_hours is not None and interval_hours < 1:
        raise ValueError(f"interval_hours must be >= 1, got {interval_hours}")
    cfg = load_config()
    # Two concurrent pruners race on the archive files (JSONL/Parquet appends),
    # so the daemon holds an exclusive home-dir lock for its lifetime.
    lock_path = cfg.home / ".pruner.lock"
    cfg.home.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(lock_fd)
        print(
            json.dumps(
                {"ok": False, "error": "pruner_already_running", "message": f"another pruner holds {lock_path}"},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 1
    try:
        _run_pruner_cycles(cfg, interval_hours=interval_hours, run_once=run_once, max_cycles=max_cycles)
        return 0
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _run_pruner_cycles(cfg: ForgetForgeConfig, *, interval_hours: int | None, run_once: bool, max_cycles: int) -> None:
    hours = interval_hours or cfg.pruner_interval_hours
    seconds = max(60, int(hours * 3600))
    cycles = 1 if run_once else max(1, max_cycles)
    for index in range(cycles):
        started = time.monotonic()
        conn = db.connect(cfg.db_path)
        try:
            summary = run_pruner(conn, config=cfg)
        finally:
            conn.close()
        summary["interval_hours"] = hours
        summary["cycle"] = index + 1
        summary["cycles_max"] = cycles
        summary["duration_ms"] = int((time.monotonic() - started) * 1000)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if run_once:
            return
        if index == cycles - 1:
            return
        time.sleep(seconds)


__all__ = ["run_pruner", "run_pruner_daemon"]
