"""Parity tests: every available engine backend must score identically."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from forgetforge import rust_bridge

_LOCAL_BIN = (
    Path(__file__).resolve().parents[1] / "rust" / "forgetforge_engine" / "target" / "release" / "forgetforge-engine"
)

BACKENDS = ["python"]
if importlib.util.find_spec("forgetforge_engine_native") is not None:
    BACKENDS.append("native")
if _LOCAL_BIN.exists() or shutil.which("forgetforge-engine"):
    BACKENDS.append("subprocess")

_SCORE_CASES = [
    {"days_since_recall": 0.0, "retrieval_count": 0.0, "importance": 0.0, "frequency": 0.0},
    {"days_since_recall": 3.0, "retrieval_count": 2.0, "importance": 0.5, "frequency": 0.2},
    {"days_since_recall": 45.0, "retrieval_count": 9.0, "importance": 1.0, "frequency": 1.0},
]

_TIER_CASES = [
    {"days_since_recall": 1.0, "retrieval_count": 4.0, "importance": 0.9, "frequency": 0.8},
    {"days_since_recall": 20.0, "retrieval_count": 1.0, "importance": 0.3, "frequency": 0.1},
    {"days_since_recall": 200.0, "retrieval_count": 0.0, "importance": 0.1, "frequency": 0.0},
    {
        "days_since_recall": 10.0,
        "retrieval_count": 5.0,
        "importance": 0.4,
        "frequency": 0.3,
        "is_procedural": True,
    },
    {"days_since_recall": 99.0, "retrieval_count": 0.0, "importance": 0.0, "frequency": 0.0, "keep_forever": True},
]


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch):
    monkeypatch.setenv(rust_bridge.ENGINE_BACKEND_ENV, request.param)
    if request.param == "subprocess" and _LOCAL_BIN.exists():
        monkeypatch.setenv(rust_bridge.ENGINE_BIN_ENV, str(_LOCAL_BIN))
    return request.param


def test_resolve_backend_honors_env(backend: str) -> None:
    assert rust_bridge.resolve_backend() == backend


def test_engine_available_tracks_backend(backend: str) -> None:
    assert rust_bridge.engine_available() == (backend != "python")


@pytest.mark.parametrize("case", _SCORE_CASES)
def test_score_parity_with_python(backend: str, case: dict[str, float]) -> None:
    scored = rust_bridge.compute_retention(**case)
    expected = rust_bridge._python_retention(dict(case))
    for key in ("retention", "stability", "boost"):
        assert scored[key] == pytest.approx(expected[key], rel=1e-9)


@pytest.mark.parametrize("case", _TIER_CASES)
def test_tier_parity_with_python(backend: str, case: dict[str, object]) -> None:
    decision = rust_bridge.decide_tier(**case)
    expected_payload = {"is_procedural": False, "keep_forever": False, **case}
    expected = rust_bridge._python_tier(expected_payload)
    assert decision["tier"] == expected["tier"]
    assert decision["action"] == expected["action"]
    assert decision["retention"] == pytest.approx(expected["retention"], rel=1e-9)


def test_tier_batch_matches_single_calls(backend: str) -> None:
    batch = rust_bridge.decide_tier_batch([dict(case) for case in _TIER_CASES])
    assert len(batch) == len(_TIER_CASES)
    for case, decision in zip(_TIER_CASES, batch, strict=True):
        single = rust_bridge.decide_tier(**case)
        assert decision["tier"] == single["tier"]
        assert decision["action"] == single["action"]
        assert decision["retention"] == pytest.approx(single["retention"], rel=1e-9)


def test_tier_batch_empty_is_empty(backend: str) -> None:
    assert rust_bridge.decide_tier_batch([]) == []


def test_resolve_backend_is_cached(monkeypatch):
    """Ensure resolve_backend memoizes and shutil.which is called at most once."""
    import shutil as real_shutil

    call_count = 0

    original_which = real_shutil.which
    def counting_which(cmd):
        nonlocal call_count
        call_count += 1
        return original_which(cmd)

    monkeypatch.setattr(real_shutil, "which", counting_which)
    # Force python path 
    monkeypatch.delenv(rust_bridge.ENGINE_BACKEND_ENV, raising=False)
    monkeypatch.delenv(rust_bridge.ENGINE_BIN_ENV, raising=False)
    monkeypatch.setattr(rust_bridge, "_native_module", lambda: None)
    monkeypatch.setattr(rust_bridge, "_binary_available", lambda: False)
    # Clear any cache 
    rust_bridge._backend_cache = None
    rust_bridge._env_snapshot = None

    # Call multiple times
    for _ in range(5):
        b = rust_bridge.resolve_backend()
        assert b == "python"

    assert call_count <= 1, f"shutil.which called {call_count} times, expected <=1"


def test_subprocess_backend_timeout_falls_back_to_python(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def timeout_run(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout"))

    monkeypatch.setenv(rust_bridge.ENGINE_BACKEND_ENV, "subprocess")
    monkeypatch.setattr(rust_bridge, "_binary_available", lambda: True)
    monkeypatch.setattr(rust_bridge, "_binary", lambda: "forgetforge-engine")
    monkeypatch.setattr(rust_bridge.subprocess, "run", timeout_run)
    rust_bridge._backend_cache = None
    rust_bridge._env_snapshot = None

    payload = {"days_since_recall": 3.0, "retrieval_count": 2.0, "importance": 0.5, "frequency": 0.2}
    assert rust_bridge.compute_retention(**payload) == rust_bridge._python_retention(payload)
    assert seen["timeout"] == 30
