# Task: AutoClearMemory — fix 4 live-audit defects (1 P2 contradiction false-positive + 3 P3)

## Context
Installed build is 0.3.10. A live adversarial audit (running the installed site-packages build) confirmed 4 REAL defects. Fix all in repo `src/`. Do NOT regress the 12 live-verified working functions. Do NOT bump the version in pyproject (deploy handled separately). Do NOT touch `.grok-briefs/`.

## Defect 1 (P2 — USER-FACING FALSE WARNING): contradiction detection false-positives
When storing a memory, the `contradiction_warnings` exposed to the agent includes FALSE contradictions: two memories that are merely SIMILAR (high token overlap) or contain an UNRELATED negation are flagged as contradicting. An agent can then wrongly overwrite/delete a good memory or distrust correct info. The user's explicit rule: "even a small false-positive is forbidden (directly affects the user)."
Locate the contradiction-detection logic (a `negation_pair` / token-overlap heuristic in the store/scoring path). Fix:
  - `negation_pair` should fire ONLY when the two memories concern the SAME subject/predicate context (a negation token alone, anywhere, is not enough).
  - Raise the `high_token_overlap` threshold, OR relabel pure-overlap-without-negation as a non-contradiction signal (e.g. `similar_memory`) rather than `contradiction` (contradiction ≠ similarity).
  - Mark `contradiction_warnings` as ADVISORY (explicitly non-authoritative) so a host never auto-acts destructively on them.
Invariant + tests (add): two genuinely-contradicting memories (same subject, opposite claim, e.g. "bridge networking" vs "host networking" for the same service) → still flagged. Two similar-but-compatible memories, or two memories with unrelated negations → NOT flagged.

## Defect 2 (P3 — LEAK): retrieval_events table grows unbounded (no GC)
`retrieval_events` is append-only, written on every recall, and is NOT used in scoring → unbounded DB growth over long-term use (a slow quality degradation), contradicting the plugin's "prevent memory leak" intent.
Fix: add retention to the pruner — cap `retrieval_events` by age (delete rows older than X days) and/or keep only the recent-N per memory_id; OR, if it is purely audit and genuinely unused, aggregate then discard. Preserve any legitimate use.
Invariant + test: after the pruner runs, `retrieval_events` is bounded; recall/scoring behavior unchanged.

## Defect 3 (P3 — PACKAGING INTEGRITY): native dist-info RECORD hash mismatch
The loaded `.so` passes the parity check (runtime is fine), but the native dist-info `RECORD` hash does not match the force-included `.so`, so `pip check` / hash-verify / reinstall / audit tooling may report an integrity failure.
Fix: ensure the native wheel build regenerates `RECORD` using the hash of the SAME `.so` artifact that is force-included into the final wheel, so both dist-info RECORDs are consistent (likely in the repack/build script that merges the maturin native wheel into the hatchling wheel).
Invariant: hash verification / `pip check` passes for the rebuilt wheel.

## Defect 4 (P3 — DESIGN TRADE-OFF): freshly-stored high-importance memory not surfaced until first recall
A just-stored high-importance fact (e.g. "flight at 6am tomorrow") is not auto-surfaced into the `pre_llm` hot-context until an explicit recall (recall-centric design side effect). Not data loss, but conflicts with "important info preserved/surfaced immediately."
Decide and implement the lower-risk option:
  - If recall-centric gating is the INTENDED design → DOCUMENT it clearly (docstring/README) so the behavior is explicit and not mistaken for a bug; OR
  - On store, mark high-importance memories (e.g. importance >= 0.85) as born-hot/born-warm so they surface ONCE in the next hot-context window, then decay if unused.
Do not break existing recall scoring.
Invariant: a high-importance new memory is either documented as recall-gated, or surfaced once in the next hot-context.

## Done criteria
- `uv run pytest` (the runtime/memory tests) GREEN. `uv run ruff check .` pass.
- New tests for Defect 1 (false-positive cases must NOT flag; true contradiction MUST flag) and Defect 2 (pruner bounds retrieval_events).
- No version bump in pyproject. No edits under `.grok-briefs/`. Provide a concise per-defect diff summary.
