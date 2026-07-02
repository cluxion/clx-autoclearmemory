from __future__ import annotations

import json
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


def test_recall_miss_returns_actionable_hint(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "recall", "nothing-stored-yet")
    assert code == 0
    assert payload["count"] == 0
    assert payload["message"] == "no_memories_matched"


def test_keep_and_forget_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    _run(capsys, "store", "m-keep", "--content", "remember me")
    code, payload = _run(capsys, "keep", "m-keep")
    assert code == 0 and payload["ok"] is True
    assert _run(capsys, "keep", "missing")[0] == 1
    code, payload = _run(capsys, "forget", "m-keep")
    assert code == 1 and payload["ok"] is False
    assert payload["reason"] == "kept memory cannot be forgotten"
    assert _run(capsys, "forget", "missing")[0] == 1


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


def test_import_brief_stores_prefixed_memory(capsys: pytest.CaptureFixture[str]) -> None:
    code, payload = _run(capsys, "import-brief", "--source", "supercoder", "--brief", "deploy uses blue-green")
    assert code == 0 and payload["ok"] is True
    code, recalled = _run(capsys, "recall", "blue-green")
    assert code == 0 and recalled["count"] == 1
    assert recalled["results"][0]["content"].startswith("[supercoder brief]")


def test_status_reports_stats_and_backend(capsys: pytest.CaptureFixture[str]) -> None:
    _run(capsys, "store", "m-s", "--content", "anything")
    code, payload = _run(capsys, "status")
    assert code == 0 and payload["ok"] is True
    assert payload["stats"]["total_memories"] == 1
    assert payload["engine_backend"] in {"native", "subprocess", "python"}


def test_store_against_directory_db_returns_clean_error_json(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "error" in payload
    assert "error_type" in payload
