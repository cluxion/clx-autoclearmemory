# forgetforge Graph Extension — DESIGN

Analysis-driven, additive (non-breaking) extension turning forgetforge's flat memory store
into a cross-session knowledge graph with three query views. Scope covers the three requested
capabilities as ONE graph, three views.

## As-Is (grounded in the real code)

- `db.py`: SQLite + WAL + FTS5. Tables `memories`, `memories_fts`, `retrieval_events`.
- `hot_inject.build_hot_context(conn, limit=8)`: hot path — deterministic, ≤8 rows, NO LLM.
- `pruner.py`: `run_pruner` (age-based) + `run_pruner_daemon` (single-flight lock
  `pruner_already_running`, `max_cycles` cap, WAL). Reused for TTL cascade.
- `contradiction.py`: token-overlap contradiction (subject/predicate context, negation,
  substitution). Reused to auto-create `supersedes` edges between mistake nodes.
- `rust/forgetforge_engine`: scoring + tier. Untouched.

The substrate the user's request needs already exists — this extension is additive columns,
one new table, one new module, three CLI commands, one pruner branch, one doctor probe.

## Three views, one graph

Nodes = existing `memories` rows, typed. Edges = new `graph_edges` table.

| Requested item | View | Mechanism |
|---|---|---|
| ① session memory + selective recall | episodic | `session`→`task`→`file` edges; `graph-recall --session <id>` |
| ② Graph-RAG context savings + TTL cascade | semantic routing | bounded 2-hop BFS from FTS5-anchored seeds; leader delete → `expire_at` cascade |
| ③ mistake ontology, no-repeat | failure ontology | `mistake` nodes (evolved from intent-patterns rows); `supersedes` edges via contradiction.py; `graph-recall --mistakes --domain <tags>` |

## Schema (additive migration — never rewrites existing rows)

```sql
-- ALTER memories (all nullable/defaulted → existing rows unaffected)
ALTER TABLE memories ADD COLUMN node_type   TEXT NOT NULL DEFAULT 'memory';  -- memory|session|task|file|decision|mistake|entity
ALTER TABLE memories ADD COLUMN session_id  TEXT;      -- owning leader session (for TTL cascade)
ALTER TABLE memories ADD COLUMN domain_tags TEXT;      -- space-joined tags for FTS + mistake routing
ALTER TABLE memories ADD COLUMN expire_at   INTEGER;   -- unix ts; NULL = no TTL

CREATE TABLE IF NOT EXISTS graph_edges (
  src_id  INTEGER NOT NULL,
  dst_id  INTEGER NOT NULL,
  rel     TEXT NOT NULL,           -- touched|decided|failed_on|relates_to|supersedes|owns
  weight  REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (src_id, dst_id, rel)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON graph_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON graph_edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_nodes_session ON memories(session_id);
CREATE INDEX IF NOT EXISTS idx_nodes_expire  ON memories(expire_at);
```

Migration is idempotent (guarded by PRAGMA table_info check) and runs in `_ensure_fts` sibling.

## Performance contract (the hard requirement — no infinite load / no slowdown)

HOT PATH (`graph_recall`, per-turn, target <50ms):
- 1 FTS5 query for seeds → **≤SEED_MAX=12** seed nodes.
- Bounded BFS: **hops ≤ HOPS=2**, **fanout per node ≤ FANOUT=6**, **total visited ≤ VISIT_CAP=64**.
- Returns **≤ LIMIT=8** rows (feeds existing hot_inject shape).
- Zero LLM, zero network, zero recursion without a decrementing budget. A `visited` set makes
  cycles impossible; the VISIT_CAP is a second backstop.

COLD PATH (`graph_ingest`, offline/background):
- Single-flight lock (reuse pruner's lock pattern) — never two ingests at once.
- **Time cap 60s, node cap 200 per run**, incremental via watermark (last ingested ts).
- Runs detached (Stop hook `async: true` or scheduled) → never blocks a user turn.

Both caps are constants in `graph.py` and asserted by tests + a doctor probe.

## Module: `graph.py`

```
ingest(conn, nodes: list[Node], edges: list[Edge]) -> dict   # cold; caps enforced
graph_recall(conn, *, anchor_tags, session=None, mistakes=False,
             hops=2, fanout=6, limit=8) -> list[Row]          # hot; bounded BFS
expire_session(conn, session_id, grace_days=1) -> int         # ② cascade marker
_bounded_bfs(conn, seed_ids, hops, fanout, visit_cap) -> list # visited-set, no recursion
```

Mistake ingest reuses `contradiction.detect` to add `supersedes` edges (new mistake overriding
an old one) instead of duplicating — keeps the ontology self-cleaning.

## CLI (additive subcommands)

- `forgetforge graph-ingest`   — stdin JSON {nodes, edges}; cold; prints counts.
- `forgetforge graph-recall`   — `--anchor "<tags>" [--session ID] [--mistakes] [--domain ...]`; hot; prints ≤8.
- `forgetforge graph-expire-session <id>` — ② TTL cascade; marks owned nodes expire_at=now+1d.

pruner: one added branch deletes rows where `expire_at IS NOT NULL AND expire_at < now`
(reuses the existing daemon/lock/cap — no new daemon).

## Hard-test matrix (the user's explicit risk list)

1. **No infinite loop**: graph with a cycle A→B→A; assert `graph_recall` returns within VISIT_CAP and terminates (timeout-guarded test).
2. **No slowdown**: 5,000-node / 20,000-edge synthetic graph; assert `graph_recall` p95 < 50ms.
3. **Bounded fanout**: star node with 1,000 edges; assert ≤FANOUT expanded.
4. **Non-breaking migration**: open a pre-extension DB; assert existing `recall`/`hot_inject` still pass.
5. **TTL cascade**: expire_session → pruner → owned nodes gone, unrelated nodes kept.
6. **Cold-path single-flight**: two concurrent ingests → second refused, no corruption.
7. **Context savings proof**: assert `graph_recall` payload ≤ full-store payload (the ② goal).
8. **Doctor probe** `graph_bounds_enforced`: caps are the documented constants.

## Verify + release pipeline (per work/cluxion)

pytest (incl. hard tests) → doctor 0 unexpected → version bump ALL manifests → uv build+prune
→ uv tool install --force + editable → reinstall via marketplace link → git commit/push
cluxion/<repo> → GitHub Release (triggers PyPI Trusted Publishing) → SkillBook mirror push
→ config backup. Bounds constants also surfaced in the plugin's SKILL.md so the agent knows
the guarantees.
