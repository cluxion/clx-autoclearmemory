from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
from io import StringIO
from pathlib import Path

import pytest

from forgetforge import cli, db, graph, pruner
from forgetforge.config import load_config


def _hold_pruner_lock(home: Path) -> int:
    home.mkdir(parents=True, exist_ok=True)
    lock_path = home / ".pruner.lock"
    holder = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(holder, fcntl.LOCK_EX)
    return holder


def _release_fd(holder: int) -> None:
    fcntl.flock(holder, fcntl.LOCK_UN)
    os.close(holder)


def test_pruner_lock_hard_error_is_not_reported_as_contention(tmp_path: Path, monkeypatch) -> None:
    """ENOTSUP/EIO are storage failures, not evidence that another pruner owns the lock."""
    def fail_lock(_fd: int, _mode: int) -> None:
        raise OSError(errno.ENOTSUP, "locking unsupported")

    monkeypatch.setattr(pruner.fcntl, "flock", fail_lock)
    with pytest.raises(OSError, match="locking unsupported"):
        pruner.acquire_pruner_lock(tmp_path)


def test_release_pruner_lock_closes_without_explicit_unlock(tmp_path: Path, monkeypatch) -> None:
    """Committed CLI work must not turn into a second error JSON on LOCK_UN failure."""
    lock_fd = pruner.acquire_pruner_lock(tmp_path)
    assert lock_fd is not None

    def fail_if_called(_fd: int, _mode: int) -> None:
        raise AssertionError("release should rely on close, not explicit LOCK_UN")

    monkeypatch.setattr(pruner.fcntl, "flock", fail_if_called)
    pruner.release_pruner_lock(lock_fd)
    with pytest.raises(OSError):
        os.fstat(lock_fd)


def test_second_pruner_refuses_when_lock_held(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    holder = _hold_pruner_lock(cfg.home)
    try:
        code = cli.main(["pruner-daemon", "--once", "--max-cycles", "1"])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 1
        assert payload["ok"] is False
        assert payload["error"] == "pruner_already_running"
    finally:
        _release_fd(holder)


def test_pruner_runs_normally_when_lock_free(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    code = pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload["ok"] is True


def test_pruner_daemon_creates_missing_home_before_lock(tmp_path: Path, monkeypatch, capsys) -> None:
    home = tmp_path / "missing-home"
    monkeypatch.setenv("FORGETFORGE_HOME", str(home))
    code = pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload["ok"] is True
    assert (home / ".pruner.lock").exists()


def test_pruner_daemon_summary_uses_effective_interval(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    code = pruner.run_pruner_daemon(interval_hours=2, run_once=True, max_cycles=1)
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload["interval_hours"] == 2


def test_graph_ingest_refuses_when_external_lock_held_no_mutation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """External holder of .pruner.lock: graph-ingest conflicts, no DB write, no ingest call."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    cfg.home.mkdir(parents=True, exist_ok=True)
    # Pre-create DB with a baseline row so "unchanged" is measurable if connect sneaks in.
    with db.connect(cfg.db_path) as conn:
        graph.ensure_graph_schema(conn)
        before_ids = {r[0] for r in conn.execute("SELECT id FROM memories").fetchall()}
    calls: list[tuple] = []

    def _spy_ingest(conn, nodes, edges):  # type: ignore[no-untyped-def]
        calls.append((nodes, edges))
        return {"nodes": 0, "edges": 0, "skipped": 0}

    monkeypatch.setattr(graph, "ingest", _spy_ingest)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO('{"nodes":[{"id":"n-locked","content":"should not land"}],"edges":[]}'),
    )
    holder = _hold_pruner_lock(cfg.home)
    try:
        code = cli.main(["graph-ingest"])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 1
        assert payload["ok"] is False
        assert payload["error"] == "pruner_already_running"
        assert "another pruner holds" in payload["message"]
        assert calls == []
        with db.connect(cfg.db_path) as conn:
            after_ids = {r[0] for r in conn.execute("SELECT id FROM memories").fetchall()}
        assert after_ids == before_ids
        assert "n-locked" not in after_ids
    finally:
        _release_fd(holder)


def test_oneshot_prune_refuses_when_external_lock_held_no_mutation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """External holder of .pruner.lock: one-shot prune conflicts, no run_pruner, archive untouched."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    cfg.home.mkdir(parents=True, exist_ok=True)
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    archive_before = sorted(p.name for p in cfg.archive_dir.iterdir()) if cfg.archive_dir.exists() else []
    calls: list[object] = []

    def _spy_run_pruner(conn, config=None):  # type: ignore[no-untyped-def]
        calls.append(True)
        return {"ok": True, "demoted_to_cold": [], "promoted_from_cold": []}

    monkeypatch.setattr(pruner, "run_pruner", _spy_run_pruner)
    holder = _hold_pruner_lock(cfg.home)
    try:
        code = cli.main(["prune"])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 1
        assert payload["ok"] is False
        assert payload["error"] == "pruner_already_running"
        assert "another pruner holds" in payload["message"]
        assert calls == []
        archive_after = sorted(p.name for p in cfg.archive_dir.iterdir()) if cfg.archive_dir.exists() else []
        assert archive_after == archive_before
    finally:
        _release_fd(holder)


def test_daemon_lock_blocks_oneshot_prune(tmp_path: Path, monkeypatch, capsys) -> None:
    """Daemon-style lock hold blocks one-shot prune (shared single-flight)."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    acquired = pruner.acquire_pruner_lock(cfg.home)
    assert acquired is not None
    lock_fd = acquired
    try:
        code = cli.main(["prune"])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 1
        assert payload["error"] == "pruner_already_running"
    finally:
        pruner.release_pruner_lock(lock_fd)


def test_oneshot_lock_blocks_daemon(tmp_path: Path, monkeypatch, capsys) -> None:
    """One-shot-style lock hold blocks pruner-daemon."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    acquired = pruner.acquire_pruner_lock(cfg.home)
    assert acquired is not None
    lock_fd = acquired
    try:
        code = cli.main(["pruner-daemon", "--once", "--max-cycles", "1"])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 1
        assert payload["error"] == "pruner_already_running"
    finally:
        pruner.release_pruner_lock(lock_fd)


def test_prune_releases_lock_on_exception(tmp_path: Path, monkeypatch, capsys) -> None:
    """Exception during one-shot prune must release .pruner.lock for the next flight."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    real_run = pruner.run_pruner

    def _boom(conn, config=None):  # type: ignore[no-untyped-def]
        raise OSError("simulated prune failure")

    monkeypatch.setattr(pruner, "run_pruner", _boom)
    code = cli.main(["prune"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 1
    assert payload["ok"] is False
    # Restore real body; lock must already be free for the next flight.
    monkeypatch.setattr(pruner, "run_pruner", real_run)
    code2 = pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    payload2 = json.loads(capsys.readouterr().out.strip())
    assert code2 == 0
    assert payload2["ok"] is True


def test_graph_ingest_releases_lock_on_exception(tmp_path: Path, monkeypatch, capsys) -> None:
    """Exception during graph-ingest must release .pruner.lock for the next flight."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", StringIO('{"nodes":[{"id":"x","content":"c"}],"edges":[]}'))
    real_ingest = graph.ingest

    def _boom(conn, nodes, edges):  # type: ignore[no-untyped-def]
        raise OSError("simulated ingest failure")

    monkeypatch.setattr(graph, "ingest", _boom)
    code = cli.main(["graph-ingest"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 1
    assert payload["ok"] is False
    monkeypatch.setattr(graph, "ingest", real_ingest)
    code2 = pruner.run_pruner_daemon(run_once=True, max_cycles=1)
    payload2 = json.loads(capsys.readouterr().out.strip())
    assert code2 == 0
    assert payload2["ok"] is True
