"""ForgetForge — recall-centric memory plugin for universal agent environments."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cluxion-Agentplugin-AutoClearMemory")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.3.34"

__all__ = ["__version__"]
