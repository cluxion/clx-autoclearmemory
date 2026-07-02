from __future__ import annotations

import time
from typing import Any

from forgetforge import archive, db, recall, rust_bridge
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
    return {
        "ok": True,
        "demoted_to_cold": demoted,
        "promoted_from_cold": promoted,
        "interval_hours": cfg.pruner_interval_hours,
        "retrieval_events_gc": retrieval_gc,
    }


def run_pruner_daemon(*, interval_hours: int | None = None, run_once: bool = False, max_cycles: int = 24) -> None:
    """Run pruner on an interval with a hard cycle cap."""
    cfg = load_config()
    hours = interval_hours or cfg.pruner_interval_hours
    seconds = max(60, int(hours * 3600))
    cycles = 1 if run_once else max(1, max_cycles)
    for index in range(cycles):
        conn = db.connect(cfg.db_path)
        try:
            run_pruner(conn, config=cfg)
        finally:
            conn.close()
        if run_once:
            return
        if index == cycles - 1:
            return
        time.sleep(seconds)


__all__ = ["run_pruner", "run_pruner_daemon"]
