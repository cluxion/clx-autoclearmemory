from __future__ import annotations

import hashlib
import re
from typing import Any

from forgetforge import store


def _slug(source: str, brief: str) -> str:
    digest = hashlib.sha256(brief.encode("utf-8")).hexdigest()[:12]
    safe = re.sub(r"[^a-z0-9-]+", "-", source.lower()).strip("-") or "brief"
    return f"{safe}-{digest}"


def import_brief(
    conn,
    *,
    source: str,
    brief: str,
    memory_id: str | None = None,
    importance: float = 0.65,
) -> dict[str, Any]:
    """Import preprocessing/supercoder brief into episodic memory."""
    text = brief.strip()
    if not text:
        raise ValueError("brief is required")
    src = source.strip().lower() or "manual"
    if src not in {"preprocessing", "supercoder", "manual"}:
        raise ValueError("source must be preprocessing, supercoder, or manual")
    mid = memory_id.strip() if memory_id else _slug(src, text)
    prefix = {"preprocessing": "[preprocessing brief]", "supercoder": "[supercoder brief]", "manual": "[brief]"}[src]
    content = f"{prefix}\n{text}"
    stored = store.store_memory(
        conn,
        memory_id=mid,
        content=content,
        importance=importance,
        frequency=0.1,
        is_procedural=False,
    )
    return {"ok": True, "source": src, "stored": stored}


__all__ = ["import_brief"]
