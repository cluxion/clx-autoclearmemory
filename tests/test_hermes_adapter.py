from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from forgetforge.adapters import hermes

if TYPE_CHECKING:
    from pathlib import Path

_EXPECTED_TOOLS = {
    "forgetforge_store",
    "forgetforge_recall",
    "forgetforge_status",
    "forgetforge_keep",
    "forgetforge_forget",
    "forgetforge_import_brief",
    "forgetforge_hot_context",
}


class FakeCtx:
    def __init__(self, *, hooks: bool = True) -> None:
        self.tools: dict[str, object] = {}
        self.hooks: dict[str, object] = {}
        if not hooks:
            self.register_hook = None  # register() must tolerate hosts without hooks

    def register_tool(self, *, name: str, toolset: str, schema: dict, handler: object, emoji: str) -> None:
        assert toolset == "forgetforge"
        self.tools[name] = handler

    def register_hook(self, event: str, handler: object) -> None:
        self.hooks[event] = handler


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    return tmp_path


def _call(ctx: FakeCtx, tool: str, args: dict) -> dict:
    raw = ctx.tools[tool](args)
    assert isinstance(raw, str)  # hermes handlers must return JSON strings
    return json.loads(raw)


def test_register_exposes_all_tools_and_hook() -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    assert set(ctx.tools) == _EXPECTED_TOOLS
    assert "pre_llm_call" in ctx.hooks


def test_register_tolerates_host_without_hooks() -> None:
    ctx = FakeCtx(hooks=False)
    hermes.register(ctx)
    assert set(ctx.tools) == _EXPECTED_TOOLS


def test_store_recall_roundtrip_through_handlers() -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    stored = _call(ctx, "forgetforge_store", {"memory_id": "m-h1", "content": "redis runs on port 6380"})
    assert stored["ok"] is True
    recalled = _call(ctx, "forgetforge_recall", {"query": "redis", "layer": "explicit"})
    assert recalled["count"] == 1
    assert recalled["results"][0]["memory_id"] == "m-h1"


def test_validation_errors_become_json_not_exceptions() -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    for tool, args in (
        ("forgetforge_recall", {"query": "  "}),
        ("forgetforge_keep", {"memory_id": ""}),
        ("forgetforge_forget", {}),
    ):
        payload = _call(ctx, tool, args)
        assert payload["ok"] is False, tool
        assert payload["error"], tool


def test_status_reports_stats_and_backend() -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    payload = _call(ctx, "forgetforge_status", {})
    assert payload["ok"] is True
    assert payload["stats"]["total_memories"] == 0
    assert payload["engine_backend"] in {"native", "subprocess", "python"}


def test_hot_context_hook_payload_shape() -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    # Empty store: the hook must inject nothing rather than an empty block.
    assert ctx.hooks["pre_llm_call"]() == {}
    _call(ctx, "forgetforge_store", {"memory_id": "m-hot", "content": "hot fact", "importance": 0.9})
    tool_payload = _call(ctx, "forgetforge_hot_context", {"limit": 4})
    assert tool_payload["ok"] is True
    assert tool_payload["has_hot"] == bool(tool_payload["context"])


def test_schemas_are_full_function_specs() -> None:
    # The hermes registry ships each schema verbatim as {"type": "function",
    # "function": schema}; a bare parameters object reaches the model with no
    # description and no parameters key, so the full shape is a contract.

    class _Recorder:
        def __init__(self) -> None:
            self.pairs: list[tuple[str, dict]] = []

        def register_tool(self, *, name: str, schema: dict, **_: object) -> None:
            self.pairs.append((name, schema))

        register_hook = None

    recorder = _Recorder()
    hermes.register(recorder)
    assert len(recorder.pairs) == 7
    for name, schema in recorder.pairs:
        assert schema["name"] == name
        assert schema["description"].strip()
        assert schema["parameters"]["type"] == "object"
