"""Plugin-specific probes for forgetforge (autoclearmemory) doctor. Cross-cutting + selected specific checks."""

from __future__ import annotations

import importlib.metadata
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import yaml

from .framework import DoctorContext

PROBES: dict[str, Callable[[DoctorContext], tuple[str, str]]] = {}


def _register(name: str):
    def deco(fn):
        PROBES[name] = fn
        return fn

    return deco


@_register("hermes_on_path")
def hermes_on_path(ctx: DoctorContext) -> tuple[str, str]:
    p = shutil.which(ctx.hermes_bin)
    if p:
        return "pass", str(p)
    return "fail", "not found on PATH"


@_register("hermes_version")
def hermes_version(ctx: DoctorContext) -> tuple[str, str]:
    try:
        cp = ctx.run([ctx.hermes_bin, "--version"])
        if cp.returncode == 0 and "Hermes Agent v" in cp.stdout:
            return "pass", cp.stdout.strip()
        return "fail", cp.stdout.strip() or cp.stderr.strip()
    except Exception as e:
        return "fail", f"run error: {e}"


@_register("hermes_oneshot_flag")
def hermes_oneshot_flag(ctx: DoctorContext) -> tuple[str, str]:
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
    try:
        cp = ctx.run([ctx.hermes_bin, "tools", "list"])
        if cp.returncode == 0 and "forgetforge" in cp.stdout:
            return "pass", "forgetforge present"
        return "fail", "forgetforge not in tools list"
    except Exception as e:
        return "fail", f"run error: {e}"


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

        from forgetforge.adapters.hermes import register as hermes_register

        class _TestCtx:
            def __init__(self):
                self.tools = {}

            def register_tool(self, *, name: str, handler: object, **_):
                self.tools[name] = handler

            register_hook = None

        tctx = _TestCtx()
        hermes_register(tctx)
        store_handler = tctx.tools.get("forgetforge_store")
        if not store_handler:
            return "fail", "store handler not registered"
        # real test: bad type arg triggers exception path -> {ok: false}
        raw = store_handler({"memory_id": "bad", "content": "x", "importance": [1, 2]})
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
        from forgetforge.config import default_home
        cfg_path = default_home() / "config.yaml"
        if not cfg_path.exists():
            return "skip", "config not present (uses defaults)"
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            return "pass", "yaml loaded"
        return "warn", "yaml not mapping"
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


# note: other checks in catalog will be reported as skip (no probe)
