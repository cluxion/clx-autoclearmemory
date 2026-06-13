"""Hermes tool schemas exposed by the ForgetForge plugin.

Each schema is the FULL function spec the host registry ships to the model
({"name", "description", "parameters"}), matching the official hermes plugin
contract — the registry wraps it verbatim as {"type": "function", "function":
schema}, so a bare parameters object would reach the model with no
description and no parameters key.
"""

from __future__ import annotations

RECALL_SCHEMA = {
    "name": "forgetforge_recall",
    "description": "Recall stored memories matching a query; recalling reinforces retention.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Topic or keyword to recall from memory."},
            "layer": {
                "type": "string",
                "enum": ["explicit", "implicit", "reflection"],
                "description": "Retrieval layer for boost accounting.",
            },
        },
        "required": ["query"],
    },
}

STATUS_SCHEMA = {
    "name": "forgetforge_status",
    "description": "Memory health summary: counts per tier, engine backend, hot samples.",
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

KEEP_SCHEMA = {
    "name": "forgetforge_keep",
    "description": "Pin a memory as keep_forever so it is never pruned.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string"},
        },
        "required": ["memory_id"],
    },
}

FORGET_SCHEMA = {
    "name": "forgetforge_forget",
    "description": "Mark a memory for forgetting; it drops to cold tier and stops surfacing.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string"},
        },
        "required": ["memory_id"],
    },
}

STORE_SCHEMA = {
    "name": "forgetforge_store",
    "description": "Store or update a memory with importance/frequency scoring.",
    "parameters": {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": "Stable id slug, e.g. docker-setup"},
            "content": {"type": "string", "description": "Memory text to store."},
            "importance": {"type": "number", "description": "0.0..1.0, default 0.5"},
            "frequency": {"type": "number", "description": "Secondary factor 0.0..1.0"},
            "is_procedural": {"type": "boolean", "description": "Skill/procedure memory"},
        },
        "required": ["memory_id", "content"],
    },
}

IMPORT_BRIEF_SCHEMA = {
    "name": "forgetforge_import_brief",
    "description": "Import a preprocessing/supercoder session brief into long-term memory.",
    "parameters": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["preprocessing", "supercoder", "manual"],
                "description": "Brief origin plugin.",
            },
            "brief": {"type": "string", "description": "Brief text from cluxion_queue_brief or supercoder_brief."},
            "memory_id": {"type": "string", "description": "Optional stable id; auto-generated if omitted."},
            "importance": {"type": "number", "description": "Default 0.65 for session briefs."},
        },
        "required": ["source", "brief"],
    },
}

HOT_CONTEXT_SCHEMA = {
    "name": "forgetforge_hot_context",
    "description": "Render the hot-tier memory context block that is injected before LLM calls.",
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max hot memories to list (default 8)."},
        },
    },
}

__all__ = [
    "FORGET_SCHEMA",
    "HOT_CONTEXT_SCHEMA",
    "IMPORT_BRIEF_SCHEMA",
    "KEEP_SCHEMA",
    "RECALL_SCHEMA",
    "STATUS_SCHEMA",
    "STORE_SCHEMA",
]
