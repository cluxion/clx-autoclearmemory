from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from forgetforge import db, hot_inject, import_brief
from forgetforge import store as store_api
from forgetforge.config import load_config
from forgetforge.doctor import render_json, run_doctor
from forgetforge.doctor.probes import PROBES
from forgetforge.schemas import (
    FORGET_SCHEMA,
    HOT_CONTEXT_SCHEMA,
    IMPORT_BRIEF_SCHEMA,
    KEEP_SCHEMA,
    RECALL_SCHEMA,
    STATUS_SCHEMA,
    STORE_SCHEMA,
)


def register(ctx: object) -> None:
    ctx.register_tool(
        name="forgetforge_store",
        toolset="forgetforge",
        schema=STORE_SCHEMA,
        handler=_wrap(_handle_store),
        emoji="💾",
    )
    ctx.register_tool(
        name="forgetforge_recall",
        toolset="forgetforge",
        schema=RECALL_SCHEMA,
        handler=_wrap(_handle_recall),
        emoji="🧠",
    )
    ctx.register_tool(
        name="forgetforge_status",
        toolset="forgetforge",
        schema=STATUS_SCHEMA,
        handler=_wrap(_handle_status),
        emoji="📊",
    )
    ctx.register_tool(
        name="forgetforge_keep",
        toolset="forgetforge",
        schema=KEEP_SCHEMA,
        handler=_wrap(_handle_keep),
        emoji="📌",
    )
    ctx.register_tool(
        name="forgetforge_forget",
        toolset="forgetforge",
        schema=FORGET_SCHEMA,
        handler=_wrap(_handle_forget),
        emoji="🗑️",
    )
    ctx.register_tool(
        name="forgetforge_import_brief",
        toolset="forgetforge",
        schema=IMPORT_BRIEF_SCHEMA,
        handler=_wrap(_handle_import_brief),
        emoji="📥",
    )
    ctx.register_tool(
        name="forgetforge_hot_context",
        toolset="forgetforge",
        schema=HOT_CONTEXT_SCHEMA,
        handler=_wrap(_handle_hot_context),
        emoji="🔥",
    )
    # doctor tool
    DOCTOR_SCHEMA = {
        "name": "forgetforge_doctor",
        "description": "Run the embedded forgetforge doctor checks for installation, Hermes contract, DB health, and runtime integrity.",
        "parameters": {
            "type": "object",
            "properties": {
                "verbose": {"type": "boolean", "description": "Include verbose details in output"}
            },
            "additionalProperties": False,
        },
    }
    ctx.register_tool(
        name="forgetforge_doctor",
        toolset="forgetforge",
        schema=DOCTOR_SCHEMA,
        handler=_wrap(_handle_doctor),
        emoji="🩺",
    )
    register_hook = getattr(ctx, "register_hook", None)
    if callable(register_hook):
        register_hook("pre_llm_call", _pre_llm_hot_inject)


def _pre_llm_hot_inject(**_: object) -> dict[str, str]:
    try:
        return hot_inject.hot_context_payload()
    except Exception:
        return {}


def _wrap(callback: Callable[[dict[str, object]], dict[str, object]]) -> Callable[[dict[str, object]], str]:
    def handler(args: dict[str, object], **_: object) -> str:
        args = args if isinstance(args, dict) else {}
        try:
            return json.dumps(callback(args), ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True)

    return handler


def _conn():
    cfg = load_config()
    return db.connect(cfg.db_path)


def _handle_doctor(args: dict[str, object]) -> dict[str, object]:
    try:
        pkg = "forgetforge.doctor"
        import importlib.resources
        cat_path = Path(str(importlib.resources.files(pkg).joinpath("catalog.json")))
    except Exception:
        cat_path = Path(__file__).parent.parent.parent / "doctor" / "catalog.json"
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat_path,
        probes=PROBES,
        plugin="autoclearmemory",
        version=__import__("forgetforge").__version__,
    )
    # return dict so _wrap json.dumps it; include the rendered json too
    return {"ok": result.ok, "doctor_json": render_json(result), "checks_count": len(result.checks)}


def _handle_store(args: dict[str, object]) -> dict[str, object]:
    conn = _conn()
    try:
        stored = store_api.store_memory(
            conn,
            memory_id=str(args.get("memory_id", "")),
            content=str(args.get("content", "")),
            importance=float(args.get("importance", 0.5)),
            frequency=float(args.get("frequency", 0.0)),
            is_procedural=bool(args.get("is_procedural", False)),
        )
        return {"ok": True, "stored": stored}
    finally:
        conn.close()


def _handle_recall(args: dict[str, object]) -> dict[str, object]:
    query = str(args.get("query", "")).strip()
    layer = str(args.get("layer", "explicit"))
    if not query:
        raise ValueError("query is required")
    conn = _conn()
    try:
        return store_api.recall_with_feedback(conn, query, layer=layer)
    finally:
        conn.close()


def _handle_status(_: dict[str, object]) -> dict[str, object]:
    from forgetforge import rust_bridge

    conn = _conn()
    try:
        return {
            "ok": True,
            "stats": db.memory_stats(conn),
            "rust_engine": rust_bridge.engine_available(),
            "engine_backend": rust_bridge.resolve_backend(),
        }
    finally:
        conn.close()


def _handle_keep(args: dict[str, object]) -> dict[str, object]:
    memory_id = str(args.get("memory_id", "")).strip()
    if not memory_id:
        raise ValueError("memory_id is required")
    conn = _conn()
    try:
        ok = db.mark_keep_forever(conn, memory_id)
        return {"ok": ok, "memory_id": memory_id}
    finally:
        conn.close()


def _handle_forget(args: dict[str, object]) -> dict[str, object]:
    memory_id = str(args.get("memory_id", "")).strip()
    if not memory_id:
        raise ValueError("memory_id is required")
    conn = _conn()
    try:
        ok = db.mark_forget(conn, memory_id)
        return {"ok": ok, "memory_id": memory_id}
    finally:
        conn.close()


def _handle_import_brief(args: dict[str, object]) -> dict[str, object]:
    conn = _conn()
    try:
        return import_brief.import_brief(
            conn,
            source=str(args.get("source", "manual")),
            brief=str(args.get("brief", "")),
            memory_id=str(args.get("memory_id", "")).strip() or None,
            importance=float(args.get("importance", 0.65)),
        )
    finally:
        conn.close()


def _handle_hot_context(args: dict[str, object]) -> dict[str, object]:
    limit = int(args.get("limit", 8))
    conn = _conn()
    try:
        context = hot_inject.build_hot_context(conn, limit=limit)
        return {"ok": True, "context": context, "has_hot": bool(context)}
    finally:
        conn.close()


__all__ = ["register"]
