from __future__ import annotations

import argparse
import importlib.resources
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from forgetforge import __version__, db, graph, hot_inject, import_brief, init_assets, pruner, rust_bridge, store
from forgetforge.config import default_home, load_config
from forgetforge.doctor import render_json, render_text, run_doctor
from forgetforge.doctor.framework import load_catalog
from forgetforge.doctor.probes import PROBES

if TYPE_CHECKING:
    from collections.abc import Sequence


class _JsonArgparseError(Exception):
    pass


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _JsonArgparseError(message)


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = _parser(json_errors=_json_requested(raw_argv))
    try:
        args = parser.parse_args(raw_argv)
    except _JsonArgparseError as e:
        return _usage_error(str(e), error="usage_error", hint="check command syntax")
    if args.command == "check":
        return _check()
    if args.command == "init":
        return _init(args)
    if args.command == "status":
        return _status()
    if args.command == "recall":
        return _recall(args)
    if args.command == "keep":
        return _keep(args)
    if args.command == "forget":
        return _forget(args)
    if args.command == "unforget":
        return _unforget(args)
    if args.command == "list-forgotten":
        return _list_forgotten(args)
    if args.command == "prune":
        return _prune()
    if args.command == "store":
        return _store(args)
    if args.command == "pruner-daemon":
        return _pruner_daemon(args)
    if args.command == "import-brief":
        return _import_brief(args)
    if args.command == "hot-context":
        return _hot_context(args)
    if args.command == "graph-ingest":
        return _graph_ingest(args)
    if args.command == "graph-recall":
        return _graph_recall(args)
    if args.command == "graph-expire-session":
        return _graph_expire_session(args)
    if args.command == "doctor":
        return _doctor(args)
    parser.print_help(sys.stderr)
    return 2


def _json_requested(argv: Sequence[str]) -> bool:
    return any(arg in {"--json", "--json-stdin"} for arg in argv)


def _parser(*, json_errors: bool = False) -> argparse.ArgumentParser:
    parser_class = _JsonArgumentParser if json_errors else argparse.ArgumentParser
    parser = parser_class(prog="forgetforge")
    parser.add_argument("--version", action="version", version=f"forgetforge {__version__}")
    sub = parser.add_subparsers(dest="command", parser_class=parser_class)
    sub.add_parser("check", help="Check Rust engine and database paths")
    init = sub.add_parser("init", help="Initialize ~/.forgetforge and optional agent adapters")
    init.add_argument("--agents", default="all", help="Comma-separated: hermes,claude,codex or all")
    sub.add_parser("status", help="Memory health summary")
    recall_cmd = sub.add_parser("recall", help="Recall memories matching a query")
    recall_cmd.add_argument("query")
    keep = sub.add_parser("keep", help="Mark memory as keep_forever")
    keep.add_argument("memory_id")
    forget = sub.add_parser("forget", help="Mark memory for forgetting")
    forget.add_argument("memory_id")
    forget.add_argument("--force", action="store_true", help="Allow forgetting a keep_forever memory")
    unforget = sub.add_parser("unforget", help="Restore a soft-forgotten memory")
    unforget.add_argument("memory_id")
    list_forgotten = sub.add_parser("list-forgotten", help="List soft-forgotten memories for recovery")
    list_forgotten.add_argument("--limit", type=int, default=100)
    sub.add_parser("prune", help="Run background pruner once")
    gi = sub.add_parser("graph-ingest", help="Ingest graph nodes+edges from stdin JSON (cold path)")
    gi.add_argument("--stdin", action="store_true", help="Read {nodes, edges} JSON from stdin")
    gr = sub.add_parser("graph-recall", help="Bounded subgraph recall (hot path, deterministic)")
    gr.add_argument("--anchor", default="", help="FTS anchor tags")
    gr.add_argument("--session", default=None, help="Recall an episodic session's nodes")
    gr.add_argument("--mistakes", action="store_true", help="Restrict seeds to mistake nodes")
    gr.add_argument("--limit", type=int, default=graph.LIMIT)
    ge = sub.add_parser("graph-expire-session", help="TTL-cascade a deleted leader session")
    ge.add_argument("session_id")
    ge.add_argument("--grace-days", type=int, default=1)
    store_cmd = sub.add_parser("store", help="Store or update a memory")
    store_cmd.add_argument("memory_id")
    store_cmd.add_argument("--content", default=None, help="Content text, or '-' to read stdin")
    store_cmd.add_argument("--content-file", default=None, help="Read content from a UTF-8 file, or '-' for stdin")
    store_cmd.add_argument("--importance", type=float, default=0.5)
    store_cmd.add_argument("--frequency", type=float, default=0.0)
    store_cmd.add_argument("--procedural", action="store_true")
    store_cmd.add_argument(
        "--node-type",
        default=None,
        choices=sorted(graph.VALID_NODE_TYPES),
        help="Graph node type; non-'memory' rows stay out of recall/hot paths (default: memory)",
    )
    store_cmd.add_argument(
        "--expire-days", type=int, default=None, help="Hard-delete after N days via the pruner TTL sweep"
    )
    daemon = sub.add_parser("pruner-daemon", help="Run pruner on interval (background)")
    daemon.add_argument("--interval-hours", type=int, default=None)
    daemon.add_argument("--once", action="store_true", help="Run one cycle then exit")
    daemon.add_argument("--max-cycles", type=int, default=24, help="Maximum cycles before exiting")
    brief = sub.add_parser("import-brief", help="Import preprocessing/supercoder brief into memory")
    brief.add_argument("--source", choices=["preprocessing", "supercoder", "manual"], required=True)
    brief.add_argument("--brief", default=None, help="Brief text, or '-' to read stdin")
    brief.add_argument("--brief-file", default=None, help="Read brief from a UTF-8 file, or '-' for stdin")
    brief.add_argument("--memory-id", default=None)
    brief.add_argument("--importance", type=float, default=0.65)
    hot = sub.add_parser("hot-context", help="Print hot-tier context block")
    hot.add_argument("--limit", type=int, default=8)
    doctor = sub.add_parser("doctor", help="Run embedded doctor checks")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--verbose", action="store_true")
    return parser


def _check() -> int:
    home = default_home()
    payload = {
        "ok": True,
        "home": str(home),
        "rust_engine": rust_bridge.engine_available(),
        "engine_backend": rust_bridge.resolve_backend(),
        "db_exists": (home / "db.sqlite").exists(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _usage_error(message: str, *, error: str = "invalid_argument", hint: str = "check required CLI arguments") -> int:
    print(json.dumps({"ok": False, "error": error, "message": message, "hint": hint}, ensure_ascii=False))
    return 2


def _domain_error(error: str, message: str, hint: str) -> int:
    print(json.dumps({"ok": False, "error": error, "message": message, "hint": hint}, ensure_ascii=False))
    return 1


def _storage_error(e: Exception) -> int:
    return _domain_error("storage_error", str(e), "check FORGETFORGE_HOME and database permissions")


def _memory_not_found(memory_id: str) -> int:
    return _domain_error("memory_not_found", f"memory not found: {memory_id}", "check memory_id or run list-forgotten")


def _key_error(e: KeyError) -> int:
    return _domain_error("memory_not_found", str(e).strip("'\""), "check memory_id or run list-forgotten")


def _read_text_argument(name: str, value: str | None, file_path: str | None) -> str:
    option = f"--{name}"
    file_option = f"--{name}-file"
    if value is not None and file_path is not None:
        raise ValueError(f"use only one of {option} or {file_option}")
    if value is None and file_path is None:
        raise ValueError(f"{name} is required")
    if value == "-" or file_path == "-":
        return sys.stdin.read()
    if file_path is not None:
        return Path(file_path).read_text(encoding="utf-8")
    return str(value)


def _init(args: argparse.Namespace) -> int:
    try:
        agents = _parse_agents(str(args.agents))
    except ValueError as e:
        valid = ", ".join(sorted(init_assets.known_agents()))
        return _usage_error(
            str(e),
            error="unknown_agents",
            hint=f"valid values: all, {valid}. codex/claude install via their plugin marketplaces, not init.",
        )
    cfg = load_config()
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    with closing(db.connect(cfg.db_path)):
        pass  # create schema up front so later commands never race on DDL
    installed = init_assets.install_adapter_assets(agents, cfg.home)
    target = cfg.home / "config.yaml"
    config_created = init_assets.install_example_config(target)
    print(
        json.dumps(
            {
                "ok": True,
                "home": str(cfg.home),
                "db": str(cfg.db_path),
                "agents": installed,
                "config": str(target),
                "config_created": config_created,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _status() -> int:
    from forgetforge import recall

    cfg = load_config()
    with closing(db.connect(cfg.db_path)) as conn:
        stats = db.memory_stats(conn)
        hot_rows = [row for row in db.list_memories(conn, limit=50) if row.tier == "hot"]
        hot = recall.score_memories(hot_rows)
    print(
        json.dumps(
            {
                "ok": True,
                "rust_engine": rust_bridge.engine_available(),
                "engine_backend": rust_bridge.resolve_backend(),
                "stats": stats,
                "hot_samples": hot[:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _recall(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            payload = store.recall_with_feedback(conn, str(args.query))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except ValueError as e:
        return _usage_error(str(e))
    except KeyError as e:
        return _key_error(e)
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _graph_ingest(args: argparse.Namespace) -> int:
    try:
        try:
            # ValueError covers JSONDecodeError and UnicodeDecodeError (bad UTF-8
            # stdin); RecursionError comes from pathologically nested JSON.
            payload = json.loads(sys.stdin.read().strip() or "{}")
        except (ValueError, RecursionError) as e:
            return _usage_error(f"invalid JSON on stdin: {e}")
        nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
        edges = payload.get("edges", []) if isinstance(payload, dict) else []
        if not isinstance(nodes, list):
            nodes = []
        if not isinstance(edges, list):
            edges = []
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            result = graph.ingest(conn, nodes, edges)
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _graph_recall(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            rows = graph.graph_recall(
                conn,
                anchor_tags=str(args.anchor),
                session=args.session,
                mistakes=bool(args.mistakes),
                limit=int(args.limit),
            )
        print(json.dumps({"ok": True, "nodes": rows}, ensure_ascii=False, indent=2))
        return 0
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _graph_expire_session(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            marked = graph.expire_session(conn, str(args.session_id), grace_days=int(args.grace_days))
        print(json.dumps({"ok": True, "marked": marked}, ensure_ascii=False))
        return 0
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _store(args: argparse.Namespace) -> int:
    try:
        content = _read_text_argument("content", args.content, args.content_file)
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            stored = store.store_memory(
                conn,
                memory_id=str(args.memory_id),
                content=content,
                importance=float(args.importance),
                frequency=float(args.frequency),
                is_procedural=bool(args.procedural),
                node_type=args.node_type,
                expire_days=int(args.expire_days) if args.expire_days is not None else None,
            )
        print(json.dumps({"ok": True, "stored": stored}, ensure_ascii=False, indent=2))
        return 0
    except ValueError as e:
        return _usage_error(str(e))
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _pruner_daemon(args: argparse.Namespace) -> int:
    try:
        return pruner.run_pruner_daemon(
            interval_hours=args.interval_hours,
            run_once=bool(args.once),
            max_cycles=int(args.max_cycles),
        )
    except ValueError as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "invalid_argument",
                    "message": str(e),
                    "hint": "use --max-cycles >= 1 and --interval-hours >= 1",
                },
                ensure_ascii=False,
            )
        )
        return 2


def _import_brief(args: argparse.Namespace) -> int:
    try:
        brief_text = _read_text_argument("brief", args.brief, args.brief_file)
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            result = import_brief.import_brief(
                conn,
                source=str(args.source),
                brief=brief_text,
                memory_id=str(args.memory_id) if args.memory_id else None,
                importance=float(args.importance),
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ValueError as e:
        return _usage_error(str(e))
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _hot_context(args: argparse.Namespace) -> int:
    cfg = load_config()
    with closing(db.connect(cfg.db_path)) as conn:
        context = hot_inject.build_hot_context(conn, limit=int(args.limit))
    print(json.dumps({"ok": True, "context": context, "has_hot": bool(context)}, ensure_ascii=False, indent=2))
    return 0


def _keep(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            ok = db.mark_keep_forever(conn, str(args.memory_id))
        if not ok:
            return _memory_not_found(str(args.memory_id))
        print(json.dumps({"ok": ok, "memory_id": args.memory_id}, ensure_ascii=False))
        return 0
    except ValueError as e:
        return _usage_error(str(e))
    except KeyError as e:
        return _key_error(e)
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _forget(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            result = db.mark_forget(conn, str(args.memory_id), force=bool(args.force))
        if not result.get("ok") and result.get("reason") == "memory not found":
            return _memory_not_found(str(args.memory_id))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    except ValueError as e:
        return _usage_error(str(e))
    except KeyError as e:
        return _key_error(e)
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _unforget(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            result = db.unforget(conn, str(args.memory_id))
        if not result.get("ok") and result.get("reason") == "memory not found":
            return _memory_not_found(str(args.memory_id))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    except ValueError as e:
        return _usage_error(str(e))
    except KeyError as e:
        return _key_error(e)
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _list_forgotten(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
        with closing(db.connect(cfg.db_path)) as conn:
            rows = db.list_forgotten_memories(conn, limit=int(args.limit))
        print(
            json.dumps(
                {
                    "ok": True,
                    "count": len(rows),
                    "memories": [
                        {
                            "memory_id": row.id,
                            "content": row.content,
                            "tier": row.tier,
                            "keep_forever": row.keep_forever,
                        }
                        for row in rows
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (sqlite3.Error, OSError) as e:
        return _storage_error(e)


def _prune() -> int:
    cfg = load_config()
    with closing(db.connect(cfg.db_path)) as conn:
        result = pruner.run_pruner(conn, config=cfg)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _doctor(args: argparse.Namespace) -> int:
    try:
        try:
            pkg = "forgetforge.doctor"
            cat_path = Path(str(importlib.resources.files(pkg).joinpath("catalog.json")))
        except Exception:
            cat_path = Path(__file__).parent / "doctor" / "catalog.json"
        result = run_doctor(
            cwd=Path.cwd(),
            catalog_path=cat_path,
            probes=PROBES,
            plugin="autoclearmemory",
            version=__version__,
        )
        if getattr(args, "json", False):
            print(render_json(result))
        else:
            cat_entries = load_catalog(cat_path)
            text = render_text(result, cat_entries, verbose=getattr(args, "verbose", False))
            print(text)
        return 0 if result.ok else 1
    except Exception as e:
        if getattr(args, "json", False):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "doctor_failed",
                        "message": str(e),
                        "hint": "run forgetforge doctor without --json for details",
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        raise


def _parse_agents(raw: str) -> list[str]:
    if raw.strip().lower() == "all":
        return list(init_assets.known_agents())
    agents = [part.strip().lower() for part in raw.split(",") if part.strip()]
    known = set(init_assets.known_agents())
    unknown = sorted(set(agents) - known)
    if not agents or unknown:
        bad = ", ".join(unknown) if unknown else "(empty)"
        raise ValueError(f"unknown agent(s): {bad}")
    return agents


if __name__ == "__main__":
    raise SystemExit(main())
