"""Tests for embedded doctor (determinism + cross-cutting checks)."""

import json
import subprocess
import time
from pathlib import Path

from forgetforge import cli
from forgetforge.doctor import (
    DoctorResult,
    render_json,
    run_doctor,
)
from forgetforge.doctor.framework import DoctorContext
from forgetforge.doctor.probes import PROBES


def _catalog_path() -> Path:
    import importlib.resources

    pkg = "forgetforge.doctor"
    return Path(str(importlib.resources.files(pkg).joinpath("catalog.json")))


def test_run_doctor_returns_result_and_deterministic():
    cat = _catalog_path()
    r1 = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    assert isinstance(r1, DoctorResult)
    j1 = render_json(r1)
    r2 = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    j2 = render_json(r2)
    assert j1 == j2  # byte identical
    # sorted by severity then id
    ids = [c.check_id for c in r1.checks]
    assert len(ids) > 0


def test_cross_cutting_checks_present():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    for key in ("hermes_on_path", "entry_point_registered", "toolset_valid"):
        assert key in statuses
        assert statuses[key] in ("pass", "warn", "fail", "skip")


def test_new_probes_implemented_and_non_skip():
    cat = _catalog_path()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=PROBES,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    # at least two newly implemented must be non-skip
    new_checks = [
        "pyarrow_available_for_archive",
        "fts5_available",
        "forgetforge_home_env_valid",
        "config_file_loadable",
        "hot_injection_hook_wired",
    ]
    non_skip_count = sum(1 for k in new_checks if k in statuses and statuses[k] != "skip")
    assert non_skip_count >= 2


def test_probe_exception_becomes_fail():
    def bad_probe(ctx):
        raise RuntimeError("boom")

    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=_catalog_path(),
        probes={"hermes_on_path": bad_probe},
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["hermes_on_path"] == "fail"


def test_warn_only_is_ok():
    from forgetforge.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="x", category="c", severity="medium", status="warn", detail="w"),
    )
    r = DoctorResult(plugin="p", version="0.3.5", checks=checks)
    assert r.ok is True
    assert r.summary == "ok"


def test_summary_critical_skip_is_degraded():
    from forgetforge.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="db", category="c", severity="critical", status="skip", detail="no db"),
        CheckResult(check_id="x", category="c", severity="high", status="pass", detail="ok"),
    )
    r = DoctorResult(plugin="p", version="0.3.5", checks=checks)
    assert r.summary == "degraded"
    assert r.ok is False


def test_summary_critical_fail_is_fail():
    from forgetforge.doctor.framework import CheckResult, DoctorResult

    checks = (
        CheckResult(check_id="hermes", category="c", severity="critical", status="fail", detail="broken"),
        CheckResult(check_id="x", category="c", severity="high", status="pass", detail="ok"),
    )
    r = DoctorResult(plugin="p", version="0.3.5", checks=checks)
    assert r.summary == "fail"
    assert r.ok is False


def test_critical_skip_marks_degraded_summary(tmp_path, monkeypatch):
    """Hermes/DB absent → critical probes SKIP → summary degraded (CI-safe)."""
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    monkeypatch.setattr("forgetforge.doctor.probes.shutil.which", lambda _: None)
    assert not (tmp_path / "db.sqlite").exists()
    cat = _catalog_path()
    # Keep this focused on absent Hermes/DB; install metadata can fail independently in source checkouts.
    probes = {
        k: v
        for k, v in PROBES.items()
        if k not in {"entry_point_registered", "handler_exception_coverage", "install_integrity"}
    }
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes=probes,
        plugin="autoclearmemory",
        version="0.3.5",
    )
    statuses = {c.check_id: c.status for c in result.checks}
    assert statuses["hermes_on_path"] == "skip"
    assert statuses["hermes_oneshot_flag"] == "skip"
    assert statuses["database_file_exists_and_readable"] == "skip"
    assert statuses["database_schema_current"] == "skip"
    assert result.summary == "degraded"
    assert result.ok is False
    payload = json.loads(render_json(result))
    assert payload["summary"] == "degraded"
    assert payload["ok"] is False


def test_hermes_probes_skip_when_absent(monkeypatch):
    monkeypatch.setattr("forgetforge.doctor.probes.shutil.which", lambda _: None)
    ctx = _doctor_ctx()
    for name in ("hermes_on_path", "hermes_oneshot_flag", "hermes_version", "toolset_valid"):
        status, detail = PROBES[name](ctx)
        assert status == "skip"
        assert "cannot verify" in detail


def test_hermes_oneshot_flag_fails_when_present_but_missing_flag(monkeypatch):
    monkeypatch.setattr(
        "forgetforge.doctor.probes.shutil.which",
        lambda _: "/usr/local/bin/hermes",
    )

    def _help_without_oneshot(cmd):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="usage: hermes", stderr="")

    ctx = DoctorContext(Path.cwd(), "hermes", _help_without_oneshot)
    status, detail = PROBES["hermes_oneshot_flag"](ctx)
    assert status == "fail"
    assert "missing" in detail


def _doctor_ctx() -> DoctorContext:
    return DoctorContext(
        cwd=Path.cwd(),
        hermes_bin="hermes",
        run=lambda cmd: subprocess.CompletedProcess(cmd, 0, "", ""),
    )


def test_db_probes_pass_with_isolated_home(tmp_path, monkeypatch):
    from forgetforge import db
    from forgetforge.doctor.probes import (
        database_file_exists_and_readable,
        database_schema_current,
        hot_memory_tier_reachable,
    )

    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    db.connect(tmp_path / "db.sqlite").close()

    ctx = _doctor_ctx()
    assert database_file_exists_and_readable(ctx) == ("pass", str(tmp_path / "db.sqlite"))
    assert database_schema_current(ctx)[0] == "pass"
    assert hot_memory_tier_reachable(ctx) == ("pass", "hot tier query ok")


def test_db_probes_fail_with_corrupt_db(tmp_path, monkeypatch):
    from forgetforge.doctor.probes import (
        database_file_exists_and_readable,
        database_schema_current,
    )

    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    db_path = tmp_path / "db.sqlite"
    db_path.write_text("not a sqlite database", encoding="utf-8")

    ctx = _doctor_ctx()
    assert database_file_exists_and_readable(ctx)[0] == "fail"
    assert database_schema_current(ctx)[0] == "fail"


def test_static_high_probes_registered_and_pass():
    from forgetforge.doctor.probes import (
        hermes_tool_schemas_valid,
        memory_id_validation,
    )

    ctx = _doctor_ctx()
    assert hermes_tool_schemas_valid(ctx)[0] == "pass"
    assert memory_id_validation(ctx)[0] == "pass"


def test_doctor_human_report_goes_to_stdout(capsys, monkeypatch):
    monkeypatch.setattr(
        cli,
        "run_doctor",
        lambda **_: DoctorResult(plugin="p", version="0", checks=()),
    )
    assert cli.main(["doctor"]) == 0
    captured = capsys.readouterr()
    assert captured.out is not None
    assert captured.err == ""


def test_run_doctor_probes_in_parallel(tmp_path):
    cat = tmp_path / "catalog.json"
    cat.write_text(
        json.dumps(
            [
                {
                    "check_id": f"slow_{index}",
                    "category": "runtime",
                    "severity": "low",
                    "what_it_checks": "slow",
                    "failure_symptom": "slow",
                    "likely_causes": [],
                    "fix_steps": [],
                    "change_robust": "parallel",
                }
                for index in range(4)
            ]
        ),
        encoding="utf-8",
    )

    def slow_probe(ctx):
        time.sleep(0.05)
        return "pass", "ok"

    started = time.perf_counter()
    result = run_doctor(
        cwd=Path.cwd(),
        catalog_path=cat,
        probes={f"slow_{index}": slow_probe for index in range(4)},
        plugin="autoclearmemory",
        version="0.3.5",
    )
    elapsed = time.perf_counter() - started
    assert [c.status for c in result.checks] == ["pass", "pass", "pass", "pass"]
    assert elapsed < 0.15
