from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from forgetforge import db, rust_bridge
from forgetforge.config import ForgetForgeConfig, load_config

LAYER_BOOST = {
    "explicit": 0.45,
    "implicit": 0.35,
    "reflection": 0.25,
}


@dataclass(frozen=True)
class RecallResult:
    memory_id: str
    content: str
    tier: str
    retention: float
    action: str
    layer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "tier": self.tier,
            "retention": self.retention,
            "action": self.action,
            "layer": self.layer,
        }


def days_since(ts: str | None) -> float:
    if not ts:
        return 999.0
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return 999.0
    delta = datetime.now(UTC) - parsed.astimezone(UTC)
    return max(0.0, delta.total_seconds() / 86_400.0)


def record_retrieval(
    conn,
    *,
    memory_id: str,
    layer: str,
    source: str | None = None,
    commit: bool = True,
) -> RecallResult | None:
    row = db.get_memory(conn, memory_id)
    if row is None or row.forget_requested:
        return None
    boost = LAYER_BOOST.get(layer, 0.10)
    conn.execute(
        """
        INSERT INTO retrieval_events (memory_id, layer, boost, source, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (memory_id, layer, boost, source, db.now_iso()),
    )
    retrieval_count = row.retrieval_count + boost
    decision = rust_bridge.decide_tier(
        days_since_recall=0.0,
        retrieval_count=retrieval_count,
        importance=row.importance,
        frequency=row.frequency,
        is_procedural=row.is_procedural,
        keep_forever=row.keep_forever,
    )
    # Helpers never commit here; this function owns the transaction so a
    # standalone call costs one fsync and recall_query batches it away.
    db.update_memory_state(
        conn,
        memory_id=memory_id,
        tier=str(decision["tier"]),
        retrieval_count=retrieval_count,
        last_recall_at=db.now_iso(),
        commit=False,
    )
    db.bump_recall_stats(conn, memory_id, layer, commit=False)
    if commit:
        conn.commit()
    return RecallResult(
        memory_id=memory_id,
        content=row.content,
        tier=str(decision["tier"]),
        retention=float(decision["retention"]),
        action=str(decision["action"]),
        layer=layer,
    )


def recall_query(
    conn, query: str, *, layer: str = "explicit", config: ForgetForgeConfig | None = None
) -> list[RecallResult]:
    _ = config or load_config()
    matches = db.search_memories(conn, query)
    results: list[RecallResult] = []
    # One transaction for the whole recall: per-row commits cost one
    # fsync each and dominated multi-match recalls.
    for row in matches:
        recorded = record_retrieval(conn, memory_id=row.id, layer=layer, source=f"recall:{query}", commit=False)
        if recorded is not None:
            results.append(recorded)
    conn.commit()
    return results


def score_memory(row: db.MemoryRow, config: ForgetForgeConfig | None = None) -> dict[str, Any]:
    _ = config or load_config()
    days = days_since(row.last_recall_at)
    scored = rust_bridge.compute_retention(
        days_since_recall=days,
        retrieval_count=row.retrieval_count,
        importance=row.importance,
        frequency=row.frequency,
    )
    decision = rust_bridge.decide_tier(
        days_since_recall=days,
        retrieval_count=row.retrieval_count,
        importance=row.importance,
        frequency=row.frequency,
        is_procedural=row.is_procedural,
        keep_forever=row.keep_forever,
    )
    return {
        "memory_id": row.id,
        "tier": decision["tier"],
        "action": decision["action"],
        "retention": scored["retention"],
        "days_since_recall": days,
    }


def score_memories(rows: list[db.MemoryRow], config: ForgetForgeConfig | None = None) -> list[dict[str, Any]]:
    """Score many memories with one engine call (tier-batch).

    Unlike score_memory, retention comes from the tier decision, so
    keep_forever rows report the pinned 1.0 instead of the raw decay.
    """
    _ = config or load_config()
    if not rows:
        return []
    days = [days_since(row.last_recall_at) for row in rows]
    decisions = rust_bridge.decide_tier_batch(
        [
            {
                "days_since_recall": day,
                "retrieval_count": row.retrieval_count,
                "importance": row.importance,
                "frequency": row.frequency,
                "is_procedural": row.is_procedural,
                "keep_forever": row.keep_forever,
            }
            for row, day in zip(rows, days, strict=True)
        ]
    )
    return [
        {
            "memory_id": row.id,
            "tier": decision["tier"],
            "action": decision["action"],
            "retention": decision["retention"],
            "days_since_recall": day,
        }
        for row, decision, day in zip(rows, decisions, days, strict=True)
    ]


__all__ = ["RecallResult", "days_since", "recall_query", "record_retrieval", "score_memories", "score_memory"]
