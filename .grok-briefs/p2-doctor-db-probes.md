# Task: Register forgetforge doctor critical DB probes + honest summary

## Context
doctor declares 23 checks but registers only 14. The UNREGISTERED include CRITICAL data-safety
probes — `database_file_exists_and_readable`, `database_schema_current` — and HIGH ones
(`hermes_tool_schemas_valid`, `hot_memory_tier_reachable`, `memory_id_validation`). framework.py:50
computes `ok = not any(status=='fail')`, so skipped criticals still report ok=True (green) while DB
integrity is never actually checked. This is a memory-safety plugin — doctor must verify the DB.

## Implement (doctor/probes.py + doctor/framework.py — follow existing patterns)
1. Register the two CRITICAL DB probes:
   - `database_file_exists_and_readable`: connect to the FORGETFORGE_HOME db and assert a trivial
     SELECT works (READ-ONLY).
   - `database_schema_current`: assert the `memories` table + expected columns (id, content,
     keep_forever, forget_requested, tier, ...) exist via PRAGMA table_info, per db.py's schema.
2. Register statically-checkable HIGH probes where feasible without external state:
   `hermes_tool_schemas_valid` (adapter tool schemas well-formed), `memory_id_validation` (id
   validation logic), `hot_memory_tier_reachable` (a tier query returns without error).
3. Make doctor SUMMARY honest: add "ok"|"degraded"|"fail" where ok is False when any CRITICAL
   catalog check is skip (unregistered) — never green while a critical DB check silently skips.
   Mirror the approach just added to supercoder's doctor/framework.py.

## Invariants (MUST hold)
- Probes READ-ONLY; use a temp db or read-only access to the real FORGETFORGE_HOME — NEVER
  mutate/delete/forget live memories.
- Existing checks stay green; no runtime behavior change to forget/unforget/pruner (already fixed).

## Tests
- `uv run --extra dev pytest` green; new probes have tests with isolated FORGETFORGE_HOME/temp db.

## Out of scope
- No version bump / build / publish.

## Done
doctor registers + passes the critical DB probes; summary downgrades to degraded when a critical
check is unregistered (no false green); tests green. Concise diff summary.
