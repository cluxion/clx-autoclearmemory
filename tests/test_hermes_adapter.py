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
    "forgetforge_unforget",
    "forgetforge_import_brief",
    "forgetforge_hot_context",
    "forgetforge_doctor",
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


def test_forget_refuses_keep_and_unforget_recovers() -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    _call(ctx, "forgetforge_store", {"memory_id": "m-pin", "content": "pinned redis port 6380"})
    _call(ctx, "forgetforge_keep", {"memory_id": "m-pin"})
    refused = _call(ctx, "forgetforge_forget", {"memory_id": "m-pin"})
    assert refused["ok"] is False
    assert refused["reason"] == "kept memory cannot be forgotten"
    _call(ctx, "forgetforge_store", {"memory_id": "m-temp", "content": "temporary redis note"})
    forgot = _call(ctx, "forgetforge_forget", {"memory_id": "m-temp"})
    assert forgot["ok"] is True
    missed = _call(ctx, "forgetforge_recall", {"query": "temporary", "layer": "explicit"})
    assert missed["count"] == 0
    restored = _call(ctx, "forgetforge_unforget", {"memory_id": "m-temp"})
    assert restored["ok"] is True
    recalled = _call(ctx, "forgetforge_recall", {"query": "temporary", "layer": "explicit"})
    assert recalled["count"] == 1
    assert recalled["results"][0]["content"] == "temporary redis note"


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
    assert len(recorder.pairs) == 9
    for name, schema in recorder.pairs:
        assert schema["name"] == name
        assert schema["description"].strip()
        assert schema["parameters"]["type"] == "object"


def test_handler_coerces_none_args_and_catches_type_errors() -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    # None args -> coerced to {}
    payload = _call(ctx, "forgetforge_status", None)  # type: ignore[arg-type]
    assert payload["ok"] is True
    # bad type in importance -> TypeError caught -> ok:false
    payload = _call(ctx, "forgetforge_store", {"memory_id": "t1", "content": "x", "importance": [1, 2]})
    assert payload["ok"] is False
    assert "error" in payload


def _assert_home_completely_empty(home: Path) -> None:
    assert list(home.iterdir()) == []
    assert not (home / ".init.lock").exists()
    assert not (home / "db.sqlite").exists()
    assert not (home / "db.sqlite-wal").exists()
    assert not (home / "db.sqlite-shm").exists()


@pytest.mark.parametrize(
    "tool,args,error_fragment",
    [
        ("forgetforge_store", {"memory_id": "", "content": "x"}, "memory_id is required"),
        ("forgetforge_store", {"memory_id": "   ", "content": "x"}, "memory_id is required"),
        ("forgetforge_store", {"memory_id": "m1", "content": ""}, "content is required"),
        ("forgetforge_store", {"memory_id": "m1", "content": "   "}, "content is required"),
        (
            "forgetforge_store",
            {"memory_id": "m1", "content": "x", "importance": float("nan")},
            "importance must be finite",
        ),
        (
            "forgetforge_store",
            {"memory_id": "m1", "content": "x", "frequency": float("inf")},
            "frequency must be finite",
        ),
        ("forgetforge_import_brief", {"source": "manual", "brief": ""}, "brief is required"),
        ("forgetforge_import_brief", {"source": "manual", "brief": "   "}, "brief is required"),
        (
            "forgetforge_import_brief",
            {"source": "not-a-source", "brief": "hello"},
            "source must be preprocessing, supercoder, or manual",
        ),
        (
            "forgetforge_import_brief",
            {"source": "manual", "brief": "hello", "importance": float("nan")},
            "importance must be finite",
        ),
    ],
)
def test_invalid_store_import_returns_ok_false_and_leaves_home_empty(
    isolated_home: Path, tool: str, args: dict, error_fragment: str
) -> None:
    ctx = FakeCtx()
    hermes.register(ctx)
    payload = _call(ctx, tool, args)
    assert payload["ok"] is False
    assert error_fragment in payload["error"]
    _assert_home_completely_empty(isolated_home)


@pytest.mark.parametrize(
    "tool,args",
    [
        (
            "forgetforge_store",
            {"memory_id": "", "content": "", "importance": [1, 2]},
        ),
        (
            "forgetforge_import_brief",
            {"source": "manual", "brief": "", "importance": object()},
        ),
    ],
)
def test_numeric_conversion_error_precedes_blank_field_and_leaves_home_empty(
    isolated_home: Path, tool: str, args: dict
) -> None:
    # Coercion is left-to-right: float(...) fails before pure blank-field validation.
    ctx = FakeCtx()
    hermes.register(ctx)
    payload = _call(ctx, tool, args)
    assert payload["ok"] is False
    assert "required" not in payload["error"]
    _assert_home_completely_empty(isolated_home)


@pytest.mark.parametrize(
    "tool,args,error_fragment",
    [
        (
            "forgetforge_store",
            {"memory_id": " ", "content": " ", "importance": float("nan"), "frequency": float("inf")},
            "memory_id is required",
        ),
        (
            "forgetforge_store",
            {"memory_id": "m1", "content": " ", "importance": float("nan"), "frequency": float("inf")},
            "content is required",
        ),
        (
            "forgetforge_store",
            {"memory_id": "m1", "content": "ok", "importance": float("nan"), "frequency": float("inf")},
            "importance must be finite",
        ),
        (
            "forgetforge_import_brief",
            {"source": "nope", "brief": " ", "importance": float("nan")},
            "brief is required",
        ),
        (
            "forgetforge_import_brief",
            {"source": "nope", "brief": "hello", "importance": float("nan")},
            "source must be preprocessing, supercoder, or manual",
        ),
    ],
)
def test_multi_invalid_store_import_validation_order(
    isolated_home: Path, tool: str, args: dict, error_fragment: str
) -> None:
    # When conversions succeed, pure validation order is fixed and home stays empty.
    ctx = FakeCtx()
    hermes.register(ctx)
    payload = _call(ctx, tool, args)
    assert payload["ok"] is False
    assert payload["error"] == error_fragment or error_fragment in payload["error"]
    if tool == "forgetforge_store" and error_fragment == "memory_id is required":
        assert payload["error"] == "memory_id is required"
    if tool == "forgetforge_import_brief" and error_fragment == "brief is required":
        assert payload["error"] == "brief is required"
    _assert_home_completely_empty(isolated_home)


@pytest.mark.parametrize(
    "field,bad_text",
    [
        ("content", "surrogate-\udc80"),
        ("content", "invalid-byte-\udcff"),
        ("memory_id", "id-\udc80"),
        ("memory_id", "id-\udcff"),
    ],
)
def test_store_rejects_non_utf8_encodable_text_and_leaves_home_empty(
    isolated_home: Path, field: str, bad_text: str
) -> None:
    # Lone surrogates / surrogateescape-style text: structured error, no DB/WAL/SHM/locks.
    ctx = FakeCtx()
    hermes.register(ctx)
    args = {"memory_id": "m-utf8", "content": "ok content"}
    args[field] = bad_text
    payload = _call(ctx, "forgetforge_store", args)
    assert payload["ok"] is False
    assert "error" in payload
    assert "UTF-8" in payload["error"] or "utf-8" in payload["error"].lower()
    _assert_home_completely_empty(isolated_home)
