# Task: Fix 2 P1 pin-protection defects in forgetforge (memory safety, live-verified)

## Context (forget is a SOFT flag — rows always preserved, good. Do NOT change that.)
- **P1-1 forget ignores keep_forever**: `src/forgetforge/db.py:298-305` `mark_forget` sets
  `forget_requested=1` even when the row has `keep_forever=1`. The memory then disappears from
  `list_memories`/`search`/`hot` (all filter `WHERE forget_requested=0`). README promises keep =
  "절대 흐려지지 않게 고정", but the code does not protect the pin. Agents call `forgetforge_forget`
  autonomously, so ONE wrong forget silently makes a user-pinned memory unreachable.
- **P1-2 no recovery path**: there is no unforget/restore/undo anywhere. Rows are preserved, but the
  only exposed way to bring a wrongly-forgotten memory back is `store` with the same id (which loses
  the original content).

## Fix
1. **P1-1 — fail-closed pin**: in `mark_forget` (db.py), if the target row has `keep_forever=1`,
   REFUSE the forget by default — return a structured failure (e.g. ok=False, reason
   "kept memory cannot be forgotten") or raise a clear error the CLI/adapter surfaces. Do NOT
   silently flip the flag. If you add an override, gate it behind an explicit `force=False` default;
   the DEFAULT path must protect the pin.
2. **P1-2 — unforget recovery (non-destructive)**: add an operation that sets `forget_requested=0`
   and restores a sane tier (per existing tier logic, e.g. back to warm/hot) WITHOUT touching
   content. Expose it at every layer forget is exposed:
   - db function (e.g. `unforget(memory_id)`),
   - CLI subcommand mirroring existing command style (e.g. `forgetforge unforget <id>`),
   - the hermes adapter tool surface IF `forget` is exposed there (mirror `forgetforge_forget`).
   Optionally add `recall --include-forgotten` (or a `list-forgotten`) so forgotten rows can be
   found for recovery.

## Invariants (MUST hold)
- No hard delete, ever. Keep the soft-flag design. Content always preserved.
- keep_forever memories CANNOT be soft-forgotten via the default path.
- forget behavior for NON-pinned memories is unchanged (still soft-flags).
- Schema-compatible; no destructive migration.

## Tests (must pass; add coverage — use an isolated FORGETFORGE_HOME/temp DB, NEVER the live store)
- forget on a keep_forever=1 memory → refused; memory stays reachable in list/search/hot.
- forget on a normal (non-pinned) memory → still soft-flags as before.
- unforget on a forgotten memory → reachable again; content byte-identical to original.
- `uv run --extra dev pytest` green (install dev extras if pytest missing).

## Out of scope (DO NOT)
- No version bump / build / wheel / pip install / publish.
- No hard-delete, no content mutation on unforget, no destructive schema migration.
- doctor probe coverage (P2, critical DB probes unregistered) is a SEPARATE task — don't risk the
  P1 fixes for it.

## Done
keep_forever memories are protected from forget (fail-closed by default); a non-destructive
unforget recovery path exists at db + CLI (+ adapter if applicable); tests prove both and all pass.
Report a concise diff summary.
