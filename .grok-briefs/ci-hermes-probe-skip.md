# Task: doctor hermes-dependent critical probes must SKIP when hermes absent + env-independent test (CI fix #2)

## Context
CI STILL fails after the DB-probe fix: `tests/test_doctor.py` test asserting summary=="degraded" gets
"fail". Root cause: even though DB probes now skip when no db, the critical probes `hermes_on_path`
(probes.py:57) and `hermes_oneshot_flag` (probes.py:76) FAIL when hermes isn't on PATH (CI build
runner has no hermes) → summary "fail". The sibling ultracode plugin already fixed this by skipping
hermes probes when hermes is absent — MIRROR that.

## Fix
1. `doctor/probes.py`: `hermes_on_path` and `hermes_oneshot_flag` (and any probe needing the hermes
   binary): when `shutil.which(ctx.hermes_bin)` is None, return `("skip", "hermes binary not on PATH
   — cannot verify")` instead of fail. FAIL only when hermes IS present but the contract is violated.
   (Keep the DB-probe skip-on-absence fix already applied.)
2. Make `tests/test_doctor.py`'s honest-summary test ENVIRONMENT-INDEPENDENT — pass whether or not
   hermes/db exist. Best: unit-test the summary computation directly (controlled statuses dict — one
   critical "skip" + rest "pass" → "degraded"; one critical "fail" → "fail"). If it runs the real
   doctor, monkeypatch `shutil.which` (hermes absent) AND use a no-db env so DB probes skip too, then
   assert degraded (not fail).

## Invariants (MUST hold)
- hermes absent → hermes probes SKIP → summary "degraded". db absent → DB probes SKIP. All present+valid → PASS.
- Critical SKIP → "degraded"; critical FAIL → "fail". forget/unforget/pruner logic UNCHANGED.

## Tests (CRITICAL — CI is NO-hermes, NO-db)
- `uv run --extra dev pytest` green REGARDLESS of whether hermes/db exist. The honest-summary test
  must NOT depend on the machine having hermes or a db.
- `uv run ruff check src tests` clean.

## Out of scope
- No version bump. No change to forget/unforget logic.

## Done
hermes-dependent probes skip when hermes absent; honest-summary test environment-independent; pytest
green with no hermes/db. Concise diff.
