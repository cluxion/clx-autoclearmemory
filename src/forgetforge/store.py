from __future__ import annotations

import math
import time
from typing import Any

from forgetforge import contradiction, db, graph, rust_bridge
from forgetforge.config import load_config

_EXPIRE_AT_UNSET = object()


def _require_utf8_encodable(value: str, field: str) -> str:
    """Reject lone surrogates / non-UTF-8-encodable text before any storage I/O."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"{field} must be UTF-8 encodable") from e
    return value


def _normalize_required_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field} is required")
    return _require_utf8_encodable(text, field)


def _require_finite_score(name: str, value: float) -> None:
    # Floats only: int is always finite; math.isfinite(huge_int) OverflowErrors.
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


def _validate_expire_days(expire_days: int | None) -> int | None:
    if expire_days is None:
        return None
    if expire_days < 0:
        raise ValueError("expire_days must be >= 0")
    expire_at = int(time.time()) + int(expire_days) * 86400
    # SQLite INTEGER is signed 64-bit; reject overflow before any write.
    if not (-(1 << 63) <= expire_at <= (1 << 63) - 1):
        raise ValueError("expire_days is too large")
    return expire_at


def store_memory(
    conn,
    *,
    memory_id: str,
    content: str,
    importance: float = 0.5,
    frequency: float = 0.0,
    is_procedural: bool = False,
    check_contradictions: bool = True,
    node_type: str | None = None,
    expire_days: int | None = None,
    session_id: str | None = None,
    _validated_expire_at: Any = _EXPIRE_AT_UNSET,
) -> dict[str, Any]:
    """Persist or update a memory. Connected AI calls this before recall.

    node_type != 'memory' (e.g. 'session' archives) keeps the row out of
    recall/hot-injection while graph paths can still reach it; expire_days
    sets expire_at so the pruner's TTL sweep hard-deletes it later.
    session_id tags the row for graph-recall --session (None preserves an
    existing value on re-store, same as node_type/expire metadata).
    """
    memory_id = _normalize_required_text(memory_id, "memory_id")
    content = _normalize_required_text(content, "content")
    if session_id is not None:
        session_id = _normalize_required_text(session_id, "session_id")
    if node_type is not None and node_type not in graph.VALID_NODE_TYPES:
        valid = ", ".join(sorted(graph.VALID_NODE_TYPES))
        raise ValueError(f"invalid node_type: {node_type} (valid: {valid})")
    _require_finite_score("importance", importance)
    _require_finite_score("frequency", frequency)
    expire_at = (
        _validate_expire_days(expire_days)
        if _validated_expire_at is _EXPIRE_AT_UNSET
        else _validated_expire_at
    )
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
        node_type=node_type,
        expire_at=expire_at,
        session_id=session_id,
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
