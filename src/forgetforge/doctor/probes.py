"""Plugin-specific probes for forgetforge (autoclearmemory) doctor. Cross-cutting + selected specific checks."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

import yaml

from .framework import DoctorContext

_EXPECTED_MEMORY_COLUMNS = frozenset(
    {
        "id",
        "content",
        "tier",
        "importance",
        "frequency",
        "retrieval_count",
        "is_procedural",
        "keep_forever",
        "forget_requested",
        "created_at",
        "updated_at",
        "last_recall_at",
    }
)
_SCHEMA_REQUIRED_KEYS = ("name", "description", "parameters")

PROBES: dict[str, Callable[[DoctorContext], tuple[str, str]]] = {}


def _register(name: str):
    def deco(fn):
        PROBES[name] = fn
        return fn

    return deco


def _forgetforge_db_path() -> Path:
    from forgetforge.config import default_home

    return default_home() / "db.sqlite"


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


_HERMES_ABSENT_DETAIL = "hermes binary not on PATH — cannot verify"


def _hermes_absent_for(ctx: DoctorContext) -> tuple[str, str] | None:
    if shutil.which(ctx.hermes_bin) is None:
        return "skip", _HERMES_ABSENT_DETAIL
    return None


@_register("hermes_on_path")
def hermes_on_path(ctx: DoctorContext) -> tuple[str, str]:
    p = shutil.which(ctx.hermes_bin)
    if p:
        return "pass", str(p)
    return "skip", _HERMES_ABSENT_DETAIL


@_register("hermes_version")
def hermes_version(ctx: DoctorContext) -> tuple[str, str]:
    if absent := _hermes_absent_for(ctx):
        return absent
    try:
        cp = ctx.run([ctx.hermes_bin, "--version"])
        if cp.returncode == 0 and "Hermes Agent v" in cp.stdout:
            return "pass", cp.stdout.strip()
        return "fail", cp.stdout.strip() or cp.stderr.strip()
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("hermes_oneshot_flag")
def hermes_oneshot_flag(ctx: DoctorContext) -> tuple[str, str]:
    if absent := _hermes_absent_for(ctx):
        return absent
    try:
        cp = ctx.run([ctx.hermes_bin, "--help"])
        out = cp.stdout + cp.stderr
        if "-z" in out and "--oneshot" in out:
            return "pass", "present"
        return "fail", "missing in --help"
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("entry_point_registered")
def entry_point_registered(ctx: DoctorContext) -> tuple[str, str]:
    try:
        eps = importlib.metadata.entry_points(group="hermes_agent.plugins")
        for ep in eps:
            if "cluxion-agentplugin-autoclearmemory" in (ep.name or "").lower() or "forgetforge" in (ep.value or ""):
                mod = ep.load()
                if hasattr(mod, "register") and callable(mod.register):
                    return "pass", ep.value or str(ep)
        return "fail", "entry point not found or register missing"
    except Exception as e:
        return "fail", f"metadata error: {e}"


@_register("toolset_valid")
def toolset_valid(ctx: DoctorContext) -> tuple[str, str]:
    if absent := _hermes_absent_for(ctx):
        return absent
    try:
        cp = ctx.run([ctx.hermes_bin, "tools", "list"])
        if cp.returncode == 0 and "forgetforge" in cp.stdout:
            return "pass", "forgetforge present"
        return "fail", "forgetforge not in tools list"
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("graph_bounds_enforced")
def graph_bounds_enforced(ctx: DoctorContext) -> tuple[str, str]:
    """The graph hot path must stay bounded: a cycle must terminate and never exceed VISIT_CAP.
    Guards against the infinite-load / slowdown failure class."""
    try:
        import sqlite3

        from forgetforge import graph

        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE memories (id TEXT PRIMARY KEY, content TEXT, tier TEXT DEFAULT 'warm_episodic',"
            " importance REAL DEFAULT 0.5, created_at TEXT, updated_at TEXT, keep_forever INTEGER DEFAULT 0,"
            " forget_requested INTEGER DEFAULT 0);"
            "CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, content, tokenize='porter');"
        )
        graph.ensure_graph_schema(conn)
        graph.ingest(
            conn,
            [{"id": x, "content": x, "domain_tags": "c"} for x in ("A", "B", "C")],
            [
                {"src": "A", "dst": "B", "rel": "relates_to"},
                {"src": "B", "dst": "C", "rel": "relates_to"},
                {"src": "C", "dst": "A", "rel": "relates_to"},
            ],
        )
        visited = graph._bounded_bfs(conn, ["A"])
        if len(visited) <= graph.VISIT_CAP and graph.HOPS <= 2 and graph.FANOUT <= 6:
            return "pass", f"bounded: hops={graph.HOPS} fanout={graph.FANOUT} visit_cap={graph.VISIT_CAP}"
        return "fail", f"bounds exceeded: visited={len(visited)}"
    except Exception as e:
        return "fail", f"graph bounds probe error: {e}"


@_register("install_integrity")
def install_integrity(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from forgetforge import __version__ as pkg_version

        dist_version = importlib.metadata.version("cluxion-Agentplugin-AutoClearMemory")
        if dist_version == pkg_version:
            return "pass", dist_version
        return "warn", f"dist={dist_version} pkg={pkg_version}"
    except Exception as e:
        return "fail", f"version error: {e}"


@_register("native_module_importable")
def native_module_importable(ctx: DoctorContext) -> tuple[str, str]:
    try:
        mod = __import__("forgetforge_engine_native")
        if hasattr(mod, "run"):
            return "pass", "imported (native backend available)"
        return "warn", "imported but expected symbols missing"
    except Exception:
        return "warn", "native missing → using fallback (slower)"


# plugin-specific probes (deterministic ones only)
@_register("handler_exception_coverage")
def handler_exception_coverage(ctx: DoctorContext) -> tuple[str, str]:
    try:
        import json

        class _TestCtx:
            def __init__(self):
                self.tools = {}

            def register_tool(self, *, name: str, handler: object, **_):
                self.tools[name] = handler

            register_hook = None

        from forgetforge.adapters.hermes import _wrap

        # The contract under test is the _wrap boundary: any handler exception
        # must surface as {ok: false} JSON. A doctor probe must never touch the
        # user's real memory store, so a raising stub stands in for the handler
        # (store input validation itself is covered by memory_id_validation).
        def _raising_handler(args: dict[str, object]) -> dict[str, object]:
            raise TypeError("doctor-probe simulated failure")

        raw = _wrap(_raising_handler)({})
        payload = json.loads(raw)
        if payload.get("ok") is False:
            return "pass", "exception coverage verified (TypeError -> ok:false)"
        return "fail", f"expected ok:false, got {payload}"
    except Exception as e:
        return "fail", f"probe error: {type(e).__name__}: {e}"


# NEW deterministic probes for previously-skipped checks (real checks only, skip on uncertainty)
@_register("pyarrow_available_for_archive")
def pyarrow_available_for_archive(ctx: DoctorContext) -> tuple[str, str]:
    try:
        import pyarrow
        import pyarrow.parquet as pq  # noqa: F401

        return "pass", f"pyarrow {pyarrow.__version__}"
    except Exception:
        return "warn", "optional, not installed"


@_register("fts5_available")
def fts5_available(ctx: DoctorContext) -> tuple[str, str]:
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(c)")
            conn.execute("DROP TABLE t")
            return "pass", "fts5 supported in sqlite"
        finally:
            conn.close()
    except Exception as e:
        return "warn", f"fts5 missing or error: {type(e).__name__}"


@_register("wal_mode_enabled")
def wal_mode_enabled(ctx: DoctorContext) -> tuple[str, str]:
    # real check on actual db if exists, else skip (uncertainty)
    try:
        from forgetforge.config import default_home

        home = default_home()
        db_path = home / "db.sqlite"
        if not db_path.exists():
            return "skip", "db not present yet"
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if isinstance(mode, str) and mode.lower() == "wal":
                return "pass", "wal"
            return "warn", f"mode={mode}"
        finally:
            conn.close()
    except Exception as e:
        return "skip", f"cannot check wal: {type(e).__name__}"


@_register("forgetforge_home_env_valid")
def forgetforge_home_env_valid(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from forgetforge.config import default_home

        home = default_home()
        # check if can create subdir (real check)
        test_sub = home / ".doctor_probe"
        test_sub.mkdir(parents=True, exist_ok=True)
        test_sub.rmdir()
        return "pass", str(home)
    except Exception as e:
        return "skip", f"env/path issue: {type(e).__name__}"


@_register("config_file_loadable")
def config_file_loadable(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from forgetforge.config import default_home, load_config

        cfg_path = default_home() / "config.yaml"
        if not cfg_path.exists():
            return "skip", "config not present (uses defaults)"
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return "warn", "yaml not mapping"
        try:
            load_config(cfg_path)
        except (ValueError, TypeError) as e:
            return "fail", f"config values not usable: {type(e).__name__}: {e}"
        return "pass", "yaml loaded and values usable"
    except Exception as e:
        return "skip", f"yaml load issue: {type(e).__name__}"


@_register("hot_injection_hook_wired")
def hot_injection_hook_wired(ctx: DoctorContext) -> tuple[str, str]:
    # hermes_plugin_enabled style: check ~/.hermes/config.yaml for this plugin
    try:
        hermes_cfg = Path.home() / ".hermes" / "config.yaml"
        if not hermes_cfg.exists():
            return "skip", "config not present"
        with open(hermes_cfg, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        plugins = (data or {}).get("plugins", {}) or {}
        enabled = plugins.get("enabled", []) or []
        if any("autoclearmemory" in str(e).lower() or "forgetforge" in str(e).lower() for e in enabled):
            return "pass", "plugin enabled in hermes"
        return "warn", "not in plugins.enabled"
    except Exception as e:
        return "skip", f"hermes config read error: {type(e).__name__}"


@_register("database_file_exists_and_readable")
def database_file_exists_and_readable(ctx: DoctorContext) -> tuple[str, str]:
    db_path = _forgetforge_db_path()
    if not db_path.exists():
        return "skip", "no database yet — nothing to verify"
    try:
        conn = _connect_readonly(db_path)
        try:
            conn.execute("SELECT 1").fetchone()
            return "pass", str(db_path)
        finally:
            conn.close()
    except Exception as e:
        return "fail", f"read error: {type(e).__name__}: {e}"


@_register("database_schema_current")
def database_schema_current(ctx: DoctorContext) -> tuple[str, str]:
    db_path = _forgetforge_db_path()
    if not db_path.exists():
        return "skip", "no database yet — nothing to verify"
    try:
        conn = _connect_readonly(db_path)
        try:
            memory_cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
            missing = sorted(_EXPECTED_MEMORY_COLUMNS - memory_cols)
            if missing:
                return "fail", f"memories missing columns: {', '.join(missing)}"
            conn.execute("SELECT 1 FROM retrieval_events LIMIT 1")
            conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()
            return "pass", f"memories({len(memory_cols)} cols), retrieval_events, memories_fts"
        except sqlite3.OperationalError as e:
            return "fail", f"schema error: {e}"
        finally:
            conn.close()
    except Exception as e:
        return "fail", f"read error: {type(e).__name__}: {e}"


@_register("hermes_tool_schemas_valid")
def hermes_tool_schemas_valid(ctx: DoctorContext) -> tuple[str, str]:
    try:
        from forgetforge.schemas import (
            FORGET_SCHEMA,
            HOT_CONTEXT_SCHEMA,
            IMPORT_BRIEF_SCHEMA,
            KEEP_SCHEMA,
            RECALL_SCHEMA,
            STATUS_SCHEMA,
            STORE_SCHEMA,
            UNFORGET_SCHEMA,
        )

        schemas = (
            RECALL_SCHEMA,
            STATUS_SCHEMA,
            KEEP_SCHEMA,
            FORGET_SCHEMA,
            UNFORGET_SCHEMA,
            STORE_SCHEMA,
            IMPORT_BRIEF_SCHEMA,
            HOT_CONTEXT_SCHEMA,
        )
        for schema in schemas:
            for key in _SCHEMA_REQUIRED_KEYS:
                if key not in schema:
                    return "fail", f"{schema.get('name', '?')} missing {key}"
            params = schema.get("parameters")
            if not isinstance(params, dict) or params.get("type") != "object":
                return "fail", f"{schema['name']} parameters must be type object"
            json.loads(json.dumps(schema))
        return "pass", f"{len(schemas)} schemas valid"
    except Exception as e:
        return "fail", f"schema error: {type(e).__name__}: {e}"


@_register("memory_id_validation")
def memory_id_validation(ctx: DoctorContext) -> tuple[str, str]:
    from forgetforge import db, store

    try:
        empty_id_conn: object = None  # the guard must fire before any DB access
        store.store_memory(empty_id_conn, memory_id="", content="x")
        return "fail", "empty memory_id did not raise"
    except ValueError:
        pass
    except Exception as e:
        return "fail", f"unexpected error for empty id: {type(e).__name__}: {e}"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "probe.sqlite"
            conn = db.connect(db_path)
            try:
                store.store_memory(conn, memory_id="doctor-probe", content="first")
                store.store_memory(conn, memory_id="doctor-probe", content="second")
                row = db.get_memory(conn, "doctor-probe")
                if row is None or row.content != "second":
                    return "fail", "upsert did not update content"
            finally:
                conn.close()
        return "pass", "empty rejected; upsert updates content"
    except Exception as e:
        return "fail", f"validation error: {type(e).__name__}: {e}"


@_register("hot_memory_tier_reachable")
def hot_memory_tier_reachable(ctx: DoctorContext) -> tuple[str, str]:
    db_path = _forgetforge_db_path()
    if not db_path.exists():
        return "skip", "db not present yet"
    try:
        conn = _connect_readonly(db_path)
        try:
            conn.execute(
                """
                SELECT COUNT(*) FROM memories
                WHERE tier = 'hot' AND forget_requested = 0
                """
            ).fetchone()
            return "pass", "hot tier query ok"
        finally:
            conn.close()
    except Exception as e:
        return "fail", f"query error: {type(e).__name__}: {e}"


# note: other checks in catalog will be reported as skip (no probe)
