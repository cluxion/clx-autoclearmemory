"""Locate and install packaged setup assets (example config, agent adapters).

Installed wheels carry the assets under ``forgetforge/data`` (mapped in at
build time via hatch force-include); source checkouts fall back to the
repository root, so ``forgetforge init`` behaves identically in both.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from importlib.abc import Traversable

_KNOWN_AGENTS = ("hermes",)
_EXAMPLE_NAME = "config.yaml.example"


def known_agents() -> tuple[str, ...]:
    """Agents that ship adapter assets."""
    return _KNOWN_AGENTS


def install_example_config(target: Path) -> bool:
    """Copy the example config to target; never overwrite an existing file."""
    source = _asset(_EXAMPLE_NAME)
    if source is None or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    return True


def install_adapter_assets(agents: list[str], home: Path) -> dict[str, str]:
    """Copy each requested agent's adapter assets under ``home/adapters``."""
    installed: dict[str, str] = {}
    for agent in agents:
        source = _asset(f"adapters/{agent}")
        if source is None:
            continue
        target = home / "adapters" / agent
        _copy_tree(source, target)
        installed[agent] = str(target)
    return installed


def _asset(relative: str) -> Traversable | Path | None:
    packaged = resources.files("forgetforge").joinpath("data")
    for part in relative.split("/"):
        packaged = packaged.joinpath(part)
    if packaged.is_dir() or packaged.is_file():
        return packaged
    fallback = Path(__file__).resolve().parents[2] / relative
    if fallback.exists():
        return fallback
    return None


def _copy_tree(source: Traversable | Path, target: Path) -> None:
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            _copy_tree(child, target / child.name)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


__all__ = ["install_adapter_assets", "install_example_config", "known_agents"]
