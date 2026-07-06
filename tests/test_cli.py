from __future__ import annotations

import io
import json
import sys
from io import StringIO
from typing import TYPE_CHECKING

import pytest

from forgetforge import cli

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    return tmp_path


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else {})


def test_no_command_prints_help_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_check_reports_isolated_home(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, payload = _run(capsys, "check")
    assert code == 0
    assert payload["home"] == str(tmp_path)
    assert payload["db_exists"] is False


def test_init_creates_layout_and_installs_assets(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, payload = _run(capsys, "init", "--agents", "hermes")
    assert code == 0
    assert (tmp_path / "db.sqlite").exists()
    assert (tmp_path / "archive").is_dir()
    assert payload["config_created"] is True
    assert (tmp_path / "config.yaml").exists()
    assert sorted(payload["agents"]) == ["hermes"]
    assert (tmp_path / "adapters" / "hermes" / "README.md").exists()


def test_init_all_includes_every_known_agent(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "init")
    assert code == 0
    assert sorted(payload["agents"]) == ["hermes"]


def test_init_rejects_unknown_agents_as_stdout_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["init", "--agents", "nonsense"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert captured.err == ""
    assert payload["ok"] is False
    assert payload["error"] == "unknown_agents"


def test_init_never_overwrites_existing_config(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("pruner:\n  interval_hours: 1\n", encoding="utf-8")
    code, payload = _run(capsys, "init", "--agents", "hermes")
    assert code == 0
    assert payload["config_created"] is False
    assert "interval_hours: 1" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_store_then_recall_roundtrip(capsys: pytest.CaptureFixture[str]) -> None:
    code, stored = _run(capsys, "store", "m-1", "--content", "postgres port is 5433", "--importance", "0.8")
    assert code == 0 and stored["ok"] is True
    code, recalled = _run(capsys, "recall", "postgres")
    assert code == 0
    assert recalled["count"] == 1
    assert recalled["results"][0]["memory_id"] == "m-1"


def test_store_rejects_empty_memory_id_as_stdout_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["store", "", "--content", "postgres port is 5433"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert captured.err == ""
    assert payload == {
        "ok": False,
        "error": "invalid_argument",
        "message": "memory_id is required",
        "hint": "check required CLI arguments",
    }


def test_store_reads_content_from_file_and_stdin(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content_file = tmp_path / "content.txt"
    content_file.write_text("file backed memory content", encoding="utf-8")
    code, payload = _run(capsys, "store", "m-file", "--content-file", str(content_file))
    assert code == 0 and payload["ok"] is True

    monkeypatch.setattr(sys, "stdin", StringIO("stdin backed memory content"))
    code, payload = _run(capsys, "store", "m-stdin", "--content", "-")
    assert code == 0 and payload["ok"] is True

    code, recalled = _run(capsys, "recall", "backed")
    assert code == 0
    assert {row["memory_id"] for row in recalled["results"]} == {"m-file", "m-stdin"}


def test_store_session_node_type_skips_recall_but_graph_recalls(capsys: pytest.CaptureFixture[str]) -> None:
    code, stored = _run(
        capsys, "store", "sess-1", "--content", "quokka session archive", "--node-type", "session", "--expire-days", "1"
    )
    assert code == 0 and stored["ok"] is True
    code, recalled = _run(capsys, "recall", "quokka")
    assert code == 0 and recalled["count"] == 0
    code, hot = _run(capsys, "hot-context")
    assert code == 0 and "sess-1" not in hot["context"]
    code, g = _run(capsys, "graph-recall", "--anchor", "quokka")
    assert code == 0
    assert [n["id"] for n in g["nodes"]] == ["sess-1"]


def test_recall_miss_returns_actionable_hint(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "recall", "nothing-stored-yet")
    assert code == 0
    assert payload["count"] == 0
    assert payload["message"] == "no_memories_matched"


def test_keep_and_forget_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    _run(capsys, "store", "m-keep", "--content", "remember me")
    code, payload = _run(capsys, "keep", "m-keep")
    assert code == 0 and payload["ok"] is True
    code, payload = _run(capsys, "keep", "missing")
    assert code == 1
    assert payload == {
        "ok": False,
        "error": "memory_not_found",
        "message": "memory not found: missing",
        "hint": "check memory_id or run list-forgotten",
    }
    code, payload = _run(capsys, "forget", "m-keep")
    assert code == 1 and payload["ok"] is False
    assert payload["reason"] == "kept memory cannot be forgotten"
    code, payload = _run(capsys, "forget", "missing")
    assert code == 1
    assert payload["error"] == "memory_not_found"


def test_unforget_missing_memory_uses_error_contract(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "unforget", "missing")
    assert code == 1
    assert payload == {
        "ok": False,
        "error": "memory_not_found",
        "message": "memory not found: missing",
        "hint": "check memory_id or run list-forgotten",
    }


def test_forget_unforget_and_list_forgotten_cli(capsys: pytest.CaptureFixture[str]) -> None:
    _run(capsys, "store", "m-temp", "--content", "ephemeral fact about ports")
    code, payload = _run(capsys, "forget", "m-temp")
    assert code == 0 and payload["ok"] is True
    code, recalled = _run(capsys, "recall", "ephemeral")
    assert code == 0 and recalled["count"] == 0
    code, listed = _run(capsys, "list-forgotten")
    assert code == 0 and listed["count"] == 1
    assert listed["memories"][0]["memory_id"] == "m-temp"
    code, payload = _run(capsys, "unforget", "m-temp")
    assert code == 0 and payload["ok"] is True
    code, recalled = _run(capsys, "recall", "ephemeral")
    assert code == 0 and recalled["count"] == 1
    assert recalled["results"][0]["content"] == "ephemeral fact about ports"


def test_prune_runs_on_empty_db(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "prune")
    assert code == 0
    assert payload["ok"] is True


def test_hot_context_empty_db(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "hot-context")
    assert code == 0
    assert payload["has_hot"] is False
    assert payload["context"] == ""


def test_json_mode_parse_errors_use_stdout_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor", "--json", "--unknown"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert captured.err == ""
    assert payload["ok"] is False
    assert payload["error"] == "usage_error"


def test_json_mode_domain_errors_use_stdout_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_doctor(**kwargs):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(cli, "run_doctor", fail_doctor)
    code = cli.main(["doctor", "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert captured.err == ""
    assert payload == {
        "ok": False,
        "error": "doctor_failed",
        "message": "probe exploded",
        "hint": "run forgetforge doctor without --json for details",
    }


def test_import_brief_stores_prefixed_memory(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "import-brief", "--source", "supercoder", "--brief", "deploy uses blue-green")
    assert code == 0 and payload["ok"] is True
    code, recalled = _run(capsys, "recall", "blue-green")
    assert code == 0 and recalled["count"] == 1
    assert recalled["results"][0]["content"].startswith("[supercoder brief]")


def test_import_brief_rejects_empty_brief_as_stdout_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["import-brief", "--source", "manual", "--brief", ""])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 2
    assert captured.err == ""
    assert payload == {
        "ok": False,
        "error": "invalid_argument",
        "message": "brief is required",
        "hint": "check required CLI arguments",
    }


def test_import_brief_reads_brief_from_file_and_stdin(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brief_file = tmp_path / "brief.txt"
    brief_file.write_text("file brief says archive old memories", encoding="utf-8")
    code, payload = _run(capsys, "import-brief", "--source", "manual", "--brief-file", str(brief_file))
    assert code == 0 and payload["ok"] is True

    monkeypatch.setattr(sys, "stdin", StringIO("stdin brief says pin release notes"))
    code, payload = _run(capsys, "import-brief", "--source", "manual", "--brief", "-")
    assert code == 0 and payload["ok"] is True

    code, recalled = _run(capsys, "recall", "brief")
    assert code == 0
    assert recalled["count"] == 2


@pytest.mark.parametrize(
    "argv",
    [
        ("recall", "x"),
        ("keep", "x"),
        ("forget", "x"),
        ("unforget", "x"),
        ("list-forgotten",),
        ("import-brief", "--source", "manual", "--brief", "x"),
    ],
)
def test_cli_handlers_report_storage_errors(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    argv: tuple[str, ...],
) -> None:
    def fail_connect(_path):
        raise OSError("permission denied")

    monkeypatch.setattr(cli.db, "connect", fail_connect)
    code, payload = _run(capsys, *argv)
    assert code == 1
    assert payload == {
        "ok": False,
        "error": "storage_error",
        "message": "permission denied",
        "hint": "check FORGETFORGE_HOME and database permissions",
    }


def test_status_reports_stats_and_backend(capsys: pytest.CaptureFixture[str]) -> None:
    _run(capsys, "store", "m-s", "--content", "anything")
    code, payload = _run(capsys, "status")
    assert code == 0 and payload["ok"] is True
    assert payload["stats"]["total_memories"] == 1
    assert payload["engine_backend"] in {"native", "subprocess", "python"}


def test_store_against_directory_db_returns_clean_error_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Make db_path a directory to trigger connect failure
    bad_db = tmp_path / "db.sqlite"
    bad_db.mkdir()
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    # Force config to use bad path? but since load_config uses env, but db_path derived
    # Instead, directly call with monkey to simulate
    code = cli.main(["store", "m-err", "--content", "x"])
    out = capsys.readouterr().out
    assert code != 0
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error"] == "storage_error"
    assert "message" in payload
    assert "error_type" not in payload


def test_graph_ingest_invalid_utf8_stdin_returns_contract_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # UnicodeDecodeError (a ValueError) from sys.stdin.read() must stay in-contract
    bad_stdin = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{"), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", bad_stdin)
    code, payload = _run(capsys, "graph-ingest")
    assert code == 2
    assert payload["ok"] is False
    assert payload["error"] == "invalid_argument"
    assert "invalid JSON on stdin" in payload["message"]


def test_graph_ingest_deeply_nested_json_returns_contract_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # pathological nesting raises RecursionError inside json.loads
    monkeypatch.setattr(sys, "stdin", StringIO("[" * 100_000))
    code, payload = _run(capsys, "graph-ingest")
    assert code == 2
    assert payload["ok"] is False
    assert payload["error"] == "invalid_argument"


def test_graph_ingest_non_dict_items_counted_as_skipped(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # non-dict node/edge items previously escaped as AttributeError (nd.get)
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO('{"nodes": [1, "x", {"id": "n1", "content": "c"}], "edges": [2]}'),
    )
    code, payload = _run(capsys, "graph-ingest")
    assert code == 0
    assert payload["ok"] is True
    assert payload["nodes"] == 1
    assert payload["skipped"] == 3
