from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from forgetforge import __version__, db, hot_inject, import_brief, init_assets, pruner, rust_bridge, store
from forgetforge.config import default_home, load_config

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
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
    parser.print_help(sys.stderr)
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forgetforge")
    parser.add_argument("--version", action="version", version=f"forgetforge {__version__}")
    sub = parser.add_subparsers(dest="command")
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
    sub.add_parser("prune", help="Run background pruner once")
    store_cmd = sub.add_parser("store", help="Store or update a memory")
    store_cmd.add_argument("memory_id")
    store_cmd.add_argument("--content", required=True)
    store_cmd.add_argument("--importance", type=float, default=0.5)
    store_cmd.add_argument("--frequency", type=float, default=0.0)
    store_cmd.add_argument("--procedural", action="store_true")
    daemon = sub.add_parser("pruner-daemon", help="Run pruner on interval (background)")
    daemon.add_argument("--interval-hours", type=int, default=None)
    daemon.add_argument("--once", action="store_true", help="Run one cycle then exit")
    brief = sub.add_parser("import-brief", help="Import preprocessing/supercoder brief into memory")
    brief.add_argument("--source", choices=["preprocessing", "supercoder", "manual"], required=True)
    brief.add_argument("--brief", required=True)
    brief.add_argument("--memory-id", default=None)
    brief.add_argument("--importance", type=float, default=0.65)
    hot = sub.add_parser("hot-context", help="Print hot-tier context block")
    hot.add_argument("--limit", type=int, default=8)
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


def _init(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(cfg.db_path)
    conn.close()
    agents = _parse_agents(str(args.agents))
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
    conn = db.connect(cfg.db_path)
    stats = db.memory_stats(conn)
    hot_rows = [row for row in db.list_memories(conn, limit=50) if row.tier == "hot"]
    hot = recall.score_memories(hot_rows)
    conn.close()
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
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    payload = store.recall_with_feedback(conn, str(args.query))
    conn.close()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _store(args: argparse.Namespace) -> int:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    stored = store.store_memory(
        conn,
        memory_id=str(args.memory_id),
        content=str(args.content),
        importance=float(args.importance),
        frequency=float(args.frequency),
        is_procedural=bool(args.procedural),
    )
    conn.close()
    print(json.dumps({"ok": True, "stored": stored}, ensure_ascii=False, indent=2))
    return 0


def _pruner_daemon(args: argparse.Namespace) -> int:
    pruner.run_pruner_daemon(interval_hours=args.interval_hours, run_once=bool(args.once))
    return 0


def _import_brief(args: argparse.Namespace) -> int:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    result = import_brief.import_brief(
        conn,
        source=str(args.source),
        brief=str(args.brief),
        memory_id=str(args.memory_id) if args.memory_id else None,
        importance=float(args.importance),
    )
    conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _hot_context(args: argparse.Namespace) -> int:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    context = hot_inject.build_hot_context(conn, limit=int(args.limit))
    conn.close()
    print(json.dumps({"ok": True, "context": context, "has_hot": bool(context)}, ensure_ascii=False, indent=2))
    return 0


def _keep(args: argparse.Namespace) -> int:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    ok = db.mark_keep_forever(conn, str(args.memory_id))
    conn.close()
    print(json.dumps({"ok": ok, "memory_id": args.memory_id}, ensure_ascii=False))
    return 0 if ok else 1


def _forget(args: argparse.Namespace) -> int:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    ok = db.mark_forget(conn, str(args.memory_id))
    conn.close()
    print(json.dumps({"ok": ok, "memory_id": args.memory_id}, ensure_ascii=False))
    return 0 if ok else 1


def _prune() -> int:
    cfg = load_config()
    conn = db.connect(cfg.db_path)
    result = pruner.run_pruner(conn, config=cfg)
    conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _parse_agents(raw: str) -> list[str]:
    if raw.strip().lower() == "all":
        return list(init_assets.known_agents())
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
