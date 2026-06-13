from __future__ import annotations

from forgetforge import db
from forgetforge.config import load_config


def build_hot_context(conn, *, limit: int = 8) -> str:
    hot = db.list_hot_memories(conn, limit=limit)
    if not hot:
        return ""
    lines = ["[ForgetForge Hot memories — recall-centric context]"]
    for row in hot:
        preview = row.content.replace("\n", " ").strip()[:240]
        lines.append(f"- ({row.id}) {preview}")
    lines.append("[End ForgetForge Hot context]")
    return "\n".join(lines)


def hot_context_payload() -> dict[str, str]:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    try:
        context = build_hot_context(conn)
    finally:
        conn.close()
    if not context:
        return {}
    return {"context": context}


__all__ = ["build_hot_context", "hot_context_payload"]
