from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import time
from pathlib import Path
from typing import Any

from forgetforge import archive, db, graph, recall, rust_bridge
from forgetforge.config import ForgetForgeConfig, load_config


def acquire_pruner_lock(home: Path) -> int | None:
    """Nonblocking exclusive lock on ``home/.pruner.lock``.

    Returns the open lock fd on success, or ``None`` if another process holds it.
    Caller must ``release_pruner_lock`` on every path (including exceptions).
    """
    lock_path = home / ".pruner.lock"
    home.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.close(lock_fd)
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            return None
        raise
    return lock_fd


def release_pruner_lock(lock_fd: int) -> None:
    """Close a lock fd; close releases flock without a second fallible unlock step."""
    with contextlib.suppress(OSError):
        os.close(lock_fd)


def pruner_already_running_payload(lock_path: Path) -> dict[str, Any]:
    """Structured conflict contract shared by daemon, one-shot prune, and graph-ingest."""
    return {
        "ok": False,
        "error": "pruner_already_running",
        "message": f"another pruner holds {lock_path}",
    }


def emit_pruner_already_running(lock_path: Path) -> int:
    """Print the conflict JSON and return exit code 1."""
    print(json.dumps(pruner_already_running_payload(lock_path), ensure_ascii=False), flush=True)
    return 1


def run_pruner(conn, config: ForgetForgeConfig | None = None) -> dict[str, Any]:
    """Background pruner: demote low-retention memories to cold tier."""
    cfg = config or load_config()
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
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
    # Collect transitions first: archive without a write lock, then CAS-apply
    # tier changes against the pre-archive snapshot so concurrent recall cannot
    # be overwritten by a stale demotion/promotion.
    tier_updates: list[db.MemoryTierUpdate] = []
    archive_records: list[dict[str, Any]] = []
    candidate_demotions: list[str] = []
    candidate_promotions: list[str] = []
    for row, decision in zip(rows, decisions, strict=True):
        new_tier = str(decision["tier"])
        if new_tier == row.tier:
            continue
        # Full pre-archive eligibility/decision/content snapshot for CAS.
        tier_updates.append(
            (
                new_tier,
                row.id,
                row.tier,
                row.retrieval_count,
                row.importance,
                row.frequency,
                row.is_procedural,
                row.keep_forever,
                row.forget_requested,
                row.last_recall_at,
                row.updated_at,
                row.content,
            )
        )
        if new_tier == "cold":
            candidate_demotions.append(row.id)
            archive_records.append(
                {
                    "memory_id": row.id,
                    "content": row.content,
                    "retention": decision["retention"],
                    "tier": new_tier,
                }
            )
        elif row.tier == "cold":
            candidate_promotions.append(row.id)
    # Archive demotions before any tier commit. An archive failure leaves both
    # demotions and promotions at prior tiers so the next pruner retries
    # coherently. Accept possible duplicate archive if a later CAS miss or DB
    # commit fails; no outbox.
    archive.write_cold_archive_batch(cfg, archive_records)
    applied = set(db.update_memory_tiers(conn, tier_updates))
    demoted = [memory_id for memory_id in candidate_demotions if memory_id in applied]
    promoted = [memory_id for memory_id in candidate_promotions if memory_id in applied]
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
    # Same .pruner.lock is shared with one-shot prune and graph-ingest (single-flight).
    lock_fd = acquire_pruner_lock(cfg.home)
    if lock_fd is None:
        return emit_pruner_already_running(cfg.home / ".pruner.lock")
    try:
        _run_pruner_cycles(cfg, interval_hours=interval_hours, run_once=run_once, max_cycles=max_cycles)
        return 0
    finally:
        release_pruner_lock(lock_fd)


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


__all__ = [
    "acquire_pruner_lock",
    "emit_pruner_already_running",
    "pruner_already_running_payload",
    "release_pruner_lock",
    "run_pruner",
    "run_pruner_daemon",
]
