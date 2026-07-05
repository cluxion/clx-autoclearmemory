# Task: doctor critical DB probes must SKIP (not fail) when the database is absent (CI fix)

## Context
CI (no FORGETFORGE_HOME db present) failed: `tests/test_doctor.py:123` expected summary=="degraded"
but got "fail". The new critical probes `database_file_exists_and_readable` / `database_schema_current`
FAIL when the DB file doesn't exist, instead of SKIP. In a clean build/CI env the DB isn't created
yet, so these probes should SKIP (cannot verify) → summary "degraded", NOT "fail". A "fail" must mean
the DB EXISTS but is unreadable / has a wrong schema — a real defect, not a not-yet-created db.

## Fix (doctor/probes.py + tests/test_doctor.py)
- `database_file_exists_and_readable`: if the DB file does not exist, return SKIP
  ("no database yet — nothing to verify") instead of fail. FAIL only when the file exists but cannot
  be opened / queried.
- `database_schema_current`: if the DB file does not exist, SKIP. FAIL only when it exists with a
  wrong/missing schema.
- Keep summary logic: any critical SKIP → "degraded"; any critical FAIL → "fail".
- Make the env-dependent test robust: the critical-skip→degraded test must run with NO db (so the
  probe skips → degraded), independent of the CI machine. Add a separate test with a valid temp db
  asserting the probes PASS, and (optionally) one with a corrupt db asserting FAIL.

## Invariants (MUST hold)
- No-db (clean) env: DB probes SKIP → summary "degraded". Valid db: PASS. Corrupt db: FAIL.
- Probes remain READ-ONLY; never create/mutate a db as a side effect of skipping.
- No change to forget/unforget/pruner logic.

## Tests
- `uv run --extra dev pytest` green in a NO-db environment (this is what CI is). The degraded-summary
  test no longer depends on a real db.

## Out of scope
- No version bump / publish.

## Done
DB probes SKIP (not fail) when no db exists; summary degraded in clean env; tests pass without a db.
Concise diff summary.
