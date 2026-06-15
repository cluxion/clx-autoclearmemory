"""Bridge to the Rust scoring engine with a pure-Python fallback.

Backend order: native (in-process PyO3 module) -> subprocess (CLI binary)
-> python (pure fallback). Override with FORGETFORGE_ENGINE_BACKEND.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ENGINE_BACKEND_ENV = "FORGETFORGE_ENGINE_BACKEND"
ENGINE_BIN_ENV = "FORGETFORGE_ENGINE_BIN"
_BACKENDS = ("native", "subprocess", "python")

_NATIVE_UNSET = object()
_native_cache: Any = _NATIVE_UNSET

_backend_cache: str | None = None
_env_snapshot: tuple[str, str] | None = None


def resolve_backend() -> str:
    """Return the backend that will serve scoring calls."""
    global _backend_cache, _env_snapshot
    current_backend = os.environ.get(ENGINE_BACKEND_ENV, "").strip().lower()
    current_bin = os.environ.get(ENGINE_BIN_ENV, "").strip()
    current_env = (current_backend, current_bin)
    if _backend_cache is not None and _env_snapshot == current_env:
        return _backend_cache
    configured = current_backend
    if configured in _BACKENDS:
        result = configured
    elif _native_module() is not None:
        result = "native"
    elif _binary_available():
        result = "subprocess"
    else:
        result = "python"
    _backend_cache = result
    _env_snapshot = current_env
    return result


def engine_available() -> bool:
    """Check whether the Rust engine (native module or CLI) is usable."""
    return resolve_backend() != "python"


def compute_retention(
    *,
    days_since_recall: float,
    retrieval_count: float,
    importance: float,
    frequency: float,
) -> dict[str, float]:
    payload = {
        "days_since_recall": days_since_recall,
        "retrieval_count": retrieval_count,
        "importance": importance,
        "frequency": frequency,
    }
    result = _invoke("score", payload)
    if result is not None:
        return {
            "retention": float(result["retention"]),
            "stability": float(result["stability"]),
            "boost": float(result["boost"]),
        }
    return _python_retention(payload)


def decide_tier(
    *,
    days_since_recall: float,
    retrieval_count: float,
    importance: float,
    frequency: float,
    is_procedural: bool = False,
    keep_forever: bool = False,
) -> dict[str, Any]:
    payload = {
        "days_since_recall": days_since_recall,
        "retrieval_count": retrieval_count,
        "importance": importance,
        "frequency": frequency,
        "is_procedural": is_procedural,
        "keep_forever": keep_forever,
    }
    result = _invoke("tier", payload)
    if result is not None:
        return {
            "tier": str(result["tier"]),
            "action": str(result["action"]),
            "retention": float(result["retention"]),
        }
    return _python_tier(payload)


def decide_tier_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Decide tiers for many memories in one engine call.

    Amortizes the JSON boundary that dominates per-row invocations; falls
    back to per-item pure-Python scoring when no Rust backend is usable.
    """
    normalized = [
        {
            "days_since_recall": float(item["days_since_recall"]),
            "retrieval_count": float(item["retrieval_count"]),
            "importance": float(item["importance"]),
            "frequency": float(item["frequency"]),
            "is_procedural": bool(item.get("is_procedural", False)),
            "keep_forever": bool(item.get("keep_forever", False)),
        }
        for item in items
    ]
    if not normalized:
        return []
    result = _invoke("tier-batch", {"items": normalized})
    if result is not None:
        decisions = result.get("decisions")
        if isinstance(decisions, list) and len(decisions) == len(normalized):
            return [
                {
                    "tier": str(decision["tier"]),
                    "action": str(decision["action"]),
                    "retention": float(decision["retention"]),
                }
                for decision in decisions
            ]
    return [_python_tier(item) for item in normalized]


def _invoke(command: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    backend = resolve_backend()
    if backend == "native":
        return _invoke_native(command, payload)
    if backend == "subprocess":
        return _invoke_subprocess(command, payload)
    return None


def _invoke_native(command: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    module = _native_module()
    if module is None:
        return None
    try:
        outer = json.loads(module.run(command, json.dumps(payload)))
    except (RuntimeError, ValueError, TypeError):
        return None
    if not isinstance(outer, dict) or not outer.get("ok"):
        return None
    return dict(outer["result"])


def _invoke_subprocess(command: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not _binary_available():
        return None
    completed = subprocess.run(
        [_binary(), command],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    try:
        outer = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(outer, dict) or not outer.get("ok"):
        return None
    return dict(outer["result"])


def _native_module() -> Any:
    global _native_cache
    if _native_cache is _NATIVE_UNSET:
        try:
            import forgetforge_engine_native
        except ImportError:
            _native_cache = None
        else:
            _native_cache = forgetforge_engine_native
    return _native_cache


def _python_retention(payload: dict[str, float]) -> dict[str, float]:
    n_r = max(0.0, payload["retrieval_count"])
    stability = max(0.001, math.log(1.0 + n_r))
    t = max(0.0, payload["days_since_recall"])
    decay = math.exp(-t / stability)
    boost = (
        1.0
        + 0.45 * n_r
        + 0.30 * min(1.0, max(0.0, payload["importance"]))
        + 0.25 * min(1.0, max(0.0, payload["frequency"]))
    )
    retention = min(10.0, max(0.0, decay * boost))
    return {"retention": retention, "stability": stability, "boost": boost}


def _python_tier(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("keep_forever"):
        return {"tier": "warm_semantic", "action": "keep_forever_tag", "retention": 1.0}
    scored = _python_retention(payload)
    r = scored["retention"]
    n_r = float(payload["retrieval_count"])
    days = float(payload["days_since_recall"])
    if days <= 7.0 and n_r > 0.0:
        return {"tier": "hot", "action": "inject_to_prompt", "retention": r}
    if payload.get("is_procedural") and n_r >= 3.0:
        return {"tier": "warm_procedural", "action": "keep_procedural", "retention": r}
    if r >= 0.80:
        return {"tier": "warm_semantic", "action": "long_term_semantic", "retention": r}
    if r >= 0.65 and days <= 30.0:
        return {"tier": "warm_episodic", "action": "spaced_repetition", "retention": r}
    if r < 0.40 or days >= 180.0:
        return {"tier": "cold", "action": "archive_on_demand", "retention": r}
    return {"tier": "warm_episodic", "action": "maintain_warm", "retention": r}


def _binary_available() -> bool:
    binary = _binary()
    return shutil.which(binary) is not None or Path(binary).exists()


def _binary() -> str:
    configured = os.environ.get(ENGINE_BIN_ENV, "").strip()
    if configured:
        return configured
    local = (
        Path(__file__).resolve().parents[2]
        / "rust"
        / "forgetforge_engine"
        / "target"
        / "release"
        / "forgetforge-engine"
    )
    if local.exists():
        return str(local)
    return "forgetforge-engine"


__all__ = [
    "compute_retention",
    "decide_tier",
    "decide_tier_batch",
    "engine_available",
    "resolve_backend",
]
