from __future__ import annotations

from typing import Any

from forgetforge import contradiction, db, rust_bridge
from forgetforge.config import load_config


def store_memory(
    conn,
    *,
    memory_id: str,
    content: str,
    importance: float = 0.5,
    frequency: float = 0.0,
    is_procedural: bool = False,
    check_contradictions: bool = True,
) -> dict[str, Any]:
    """Persist or update a memory. Connected AI calls this before recall."""
    memory_id = memory_id.strip()
    content = content.strip()
    if not memory_id:
        raise ValueError("memory_id is required")
    if not content:
        raise ValueError("content is required")
    warnings: list[dict[str, Any]] = []
    if check_contradictions:
        warnings = [
            h.to_dict() for h in contradiction.detect_contradictions(conn, content=content, exclude_id=memory_id)
        ]
    row = db.upsert_memory(
        conn,
        memory_id=memory_id,
        content=content,
        importance=max(0.0, min(1.0, importance)),
        frequency=max(0.0, min(1.0, frequency)),
        is_procedural=is_procedural,
    )
    decision = rust_bridge.decide_tier(
        days_since_recall=999.0,
        retrieval_count=row.retrieval_count,
        importance=row.importance,
        frequency=row.frequency,
        is_procedural=row.is_procedural,
        keep_forever=row.keep_forever,
    )
    if row.tier != str(decision["tier"]):
        db.update_memory_state(
            conn,
            memory_id=row.id,
            tier=str(decision["tier"]),
            retrieval_count=row.retrieval_count,
        )
        row = db.get_memory(conn, row.id) or row
    from forgetforge import recall

    payload: dict[str, Any] = {
        "memory_id": row.id,
        "tier": row.tier,
        "retention": recall.user_retention(float(decision["retention"]), keep_forever=row.keep_forever),
        "action": str(decision["action"]),
        "content_preview": row.content[:120],
    }
    if warnings:
        payload["contradiction_warnings"] = warnings
        payload["contradiction_warnings_advisory"] = True
    if importance >= 0.85 and row.retrieval_count == 0.0:
        db.update_memory_state(
            conn,
            memory_id=row.id,
            tier="hot",
            retrieval_count=row.retrieval_count,
        )
        row = db.get_memory(conn, row.id) or row
        payload["tier"] = row.tier
        payload["retention"] = 1.0
        payload["action"] = "inject_to_prompt"
    return payload


def recall_with_feedback(
    conn,
    query: str,
    *,
    layer: str = "explicit",
) -> dict[str, Any]:
    from forgetforge import recall

    _ = load_config()
    results = recall.recall_query(conn, query, layer=layer)
    payload: dict[str, Any] = {
        "ok": True,
        "results": [r.to_dict() for r in results],
        "count": len(results),
    }
    if not results:
        payload["message"] = "no_memories_matched"
        payload["hint"] = (
            "저장된 기억이 없거나 검색어와 일치하지 않습니다. "
            "먼저 forgetforge store 또는 forgetforge_store로 기억을 저장하세요."
        )
    return payload


__all__ = ["recall_with_feedback", "store_memory"]
