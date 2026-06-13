from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from forgetforge.config import default_home, load_config

if TYPE_CHECKING:
    import pytest


def test_default_home_honours_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path / "custom"))
    assert default_home() == tmp_path / "custom"


def test_default_home_falls_back_to_user_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGETFORGE_HOME", raising=False)
    assert default_home() == Path.home() / ".forgetforge"


def test_load_config_defaults_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    cfg = load_config()
    assert cfg.home == tmp_path
    assert cfg.db_path == tmp_path / "db.sqlite"
    assert cfg.archive_dir == tmp_path / "archive"
    assert cfg.pruner_interval_hours == 6
    assert cfg.cold_retention_threshold == 0.40
    assert cfg.hot_window_days == 7
    assert cfg.no_recall_archive_days == 180


def test_load_config_reads_yaml_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "pruner:\n  interval_hours: 12\nthresholds:\n  cold_retention: 0.25\n  hot_window_days: 3\n",
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.pruner_interval_hours == 12
    assert cfg.cold_retention_threshold == 0.25
    assert cfg.hot_window_days == 3
    assert cfg.no_recall_archive_days == 180  # unspecified keys keep defaults


def test_load_config_ignores_non_mapping_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("just a scalar string\n", encoding="utf-8")
    cfg = load_config()
    assert cfg.pruner_interval_hours == 6


def test_load_config_ignores_malformed_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("pruner: not-a-mapping\nthresholds: 7\n", encoding="utf-8")
    cfg = load_config()
    assert cfg.pruner_interval_hours == 6
    assert cfg.cold_retention_threshold == 0.40
