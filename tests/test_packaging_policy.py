from __future__ import annotations

import json
import tomllib
from pathlib import Path


def test_root_plugin_artifacts_are_version_synced() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    claude = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    codex = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))

    assert claude["version"] == version
    assert codex["version"] == version
    init_src = Path("src/forgetforge/__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{version}"' in init_src  # fallback must not drift
    assert Path("commands/forgetforge-recall.md").is_file()
    assert Path("commands/forgetforge-status.md").is_file()
    assert Path("commands/forgetforge-doctor.md").is_file()
    assert Path("skills/forgetforge/SKILL.md").is_file()


def test_surface_adapter_forks_removed() -> None:
    assert not Path("adapters/claude").exists()
    assert not Path("adapters/codex").exists()


def test_marketplace_manifest_is_version_synced() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]

    marketplace = json.loads(Path(".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert marketplace["plugins"][0]["version"] == version
    assert marketplace["plugins"][0]["source"] == "./"
