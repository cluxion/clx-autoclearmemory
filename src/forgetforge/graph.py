"""Cross-session knowledge-graph layer over the flat memory store.

Additive and non-breaking: it only ALTERs `memories` with nullable/defaulted columns and
adds a `graph_edges` table. Three views on one graph — episodic (session/task/file),
semantic routing (bounded BFS), and a failure ontology (mistake nodes).

Performance contract (hard — prevents infinite-load / slowdown):
- HOT PATH `graph_recall`: 1 FTS seed query, bounded BFS (HOPS/FANOUT/VISIT_CAP), ≤LIMIT rows,
  a `visited` set so cycles are impossible, no LLM, no network, no recursion.
- COLD PATH `ingest`: node cap per run; caller holds the single-flight lock (see pruner).
All bounds are module constants asserted by tests and the `graph_bounds_enforced` doctor probe.
"""

from __future__ import annotations

import time
from typing import Any

from forgetforge import db

# --- performance bounds (the guarantees; do not raise without re-benchmarking) ---
HOPS = 2
FANOUT = 6
VISIT_CAP = 64
SEED_MAX = 12
LIMIT = 8
INGEST_NODE_CAP = 200
VALID_RELS = {"touched", "decided", "failed_on", "relates_to", "supersedes", "owns"}
VALID_NODE_TYPES = {"memory", "session", "task", "file", "decision", "mistake", "entity"}


def ensure_graph_schema(conn) -> None:
    """Idempotent additive migration. Safe on a pre-extension DB and on every call."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    add = []
    if "node_type" not in cols:
        add.append("ALTER TABLE memories ADD COLUMN node_type TEXT NOT NULL DEFAULT 'memory'")
    if "session_id" not in cols:
        add.append("ALTER TABLE memories ADD COLUMN session_id TEXT")
    if "domain_tags" not in cols:
        add.append("ALTER TABLE memories ADD COLUMN domain_tags TEXT")
    if "expire_at" not in cols:
        add.append("ALTER TABLE memories ADD COLUMN expire_at INTEGER")
    for stmt in add:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            # another writer added the column between our check and this ALTER
            # (concurrent first-time `forgetforge store` calls, e.g. SessionEnd hooks)
            if "duplicate column name" not in str(e):
                raise
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_edges (
            src_id TEXT NOT NULL,
            dst_id TEXT NOT NULL,
            rel    TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1.0,
            PRIMARY KEY (src_id, dst_id, rel)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON graph_edges(src_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON graph_edges(dst_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_session ON memories(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_expire ON memories(expire_at)")
    conn.commit()


def _now() -> int:
    return int(time.time())


def ingest(conn, nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    """Cold path. Upsert typed nodes + edges. Node cap bounds a single run.

    A node dict: {id, content, node_type?, session_id?, domain_tags?, importance?}.
    An edge dict: {src, dst, rel, weight?}. Invalid rel/type is rejected, not raised.
    Nodes beyond INGEST_NODE_CAP are dropped and counted in `skipped` (never silent) —
    callers with more should chunk into <=INGEST_NODE_CAP batches.
    """
    ensure_graph_schema(conn)
    skipped = 0
    if len(nodes) > INGEST_NODE_CAP:
        skipped = len(nodes) - INGEST_NODE_CAP  # over-cap drop is reported, not hidden
        nodes = nodes[:INGEST_NODE_CAP]
    now = db.now_iso()  # same format as regular memories share this column
    n_nodes = n_edges = 0
    for nd in nodes:
        nid = str(nd.get("id") or "").strip()
        if not nid:
            skipped += 1
            continue
        ntype = nd.get("node_type", "memory")
        if ntype not in VALID_NODE_TYPES:
            ntype = "memory"
        conn.execute(
            """
            INSERT INTO memories (id, content, tier, importance, created_at, updated_at,
                                  node_type, session_id, domain_tags)
            VALUES (?, ?, 'warm_episodic', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                content=excluded.content, updated_at=excluded.updated_at,
                node_type=excluded.node_type, session_id=excluded.session_id,
                domain_tags=excluded.domain_tags
            """,
            (
                nid,
                str(nd.get("content", "")),
                float(nd.get("importance", 0.5)),
                now,
                now,
                ntype,
                nd.get("session_id"),
                nd.get("domain_tags"),
            ),
        )
        n_nodes += 1
    for ed in edges:
        src, dst, rel = str(ed.get("src", "")), str(ed.get("dst", "")), ed.get("rel", "")
        if not src or not dst or rel not in VALID_RELS:
            skipped += 1
            continue
        conn.execute(
            """INSERT INTO graph_edges (src_id, dst_id, rel, weight) VALUES (?, ?, ?, ?)
               ON CONFLICT(src_id, dst_id, rel) DO UPDATE SET weight=excluded.weight""",
            (src, dst, rel, float(ed.get("weight", 1.0))),
        )
        n_edges += 1
    conn.commit()
    return {"nodes": n_nodes, "edges": n_edges, "skipped": skipped}


def _seed_ids(conn, anchor_tags: str, session: str | None, mistakes: bool) -> list[str]:
    """FTS-anchored seeds, hard-capped at SEED_MAX. Falls back to LIKE if FTS errors."""
    if session:
        rows = conn.execute(
            "SELECT id FROM memories WHERE session_id = ? AND forget_requested = 0 LIMIT ?",
            (session, SEED_MAX),
        ).fetchall()
        return [r[0] for r in rows]
    where_type = "AND m.node_type = 'mistake'" if mistakes else ""
    q = (anchor_tags or "").strip()
    if not q:
        return []
    ids: list[str] = []
    # FTS over content (fast, ranked). domain_tags is not in the FTS index, so also
    # match tags via LIKE and union — routing must see tags, not just prose.
    try:
        rows = conn.execute(
            f"""SELECT m.id FROM memories_fts f JOIN memories m ON m.id = f.memory_id
                WHERE memories_fts MATCH ? AND m.forget_requested = 0 {where_type}
                LIMIT ?""",
            (q, SEED_MAX),
        ).fetchall()
        ids.extend(r[0] for r in rows)
    except Exception:
        pass
    if len(ids) < SEED_MAX:
        seen = set(ids)
        for term in q.split()[:4]:
            like = f"%{term}%"
            rows = conn.execute(
                f"""SELECT m.id FROM memories m
                    WHERE m.domain_tags LIKE ? AND m.forget_requested = 0 {where_type}
                    LIMIT ?""",
                (like, SEED_MAX),
            ).fetchall()
            for (rid,) in rows:
                if rid not in seen:
                    seen.add(rid)
                    ids.append(rid)
                    if len(ids) >= SEED_MAX:
                        break
            if len(ids) >= SEED_MAX:
                break
    return ids[:SEED_MAX]


def _bounded_bfs(conn, seed_ids: list[str]) -> list[str]:
    """Iterative BFS. A `visited` set makes cycles impossible; VISIT_CAP is a second backstop.
    Never recurses. Fanout per node is truncated to FANOUT strongest edges."""
    visited: set[str] = set()
    order: list[str] = []
    frontier = list(dict.fromkeys(seed_ids))  # de-dup, preserve order
    for sid in frontier:
        if sid not in visited:
            visited.add(sid)
            order.append(sid)
    depth = 0
    current = list(order)
    while current and depth < HOPS and len(visited) < VISIT_CAP:
        nxt: list[str] = []
        for node in current:
            if len(visited) >= VISIT_CAP:
                break
            neigh = conn.execute(
                """SELECT dst_id FROM graph_edges WHERE src_id = ? ORDER BY weight DESC LIMIT ?""",
                (node, FANOUT),
            ).fetchall()
            for (dst,) in neigh:
                if dst not in visited:
                    visited.add(dst)
                    order.append(dst)
                    nxt.append(dst)
                    if len(visited) >= VISIT_CAP:
                        break
        current = nxt
        depth += 1
    return order


def graph_recall(
    conn, *, anchor_tags: str = "", session: str | None = None, mistakes: bool = False, limit: int = LIMIT
) -> list[dict[str, Any]]:
    """HOT PATH. Bounded, deterministic, no LLM. Returns ≤limit node rows most relevant to
    the anchor — the subgraph, not the whole store (the context-savings guarantee)."""
    ensure_graph_schema(conn)
    seeds = _seed_ids(conn, anchor_tags, session, mistakes)
    if not seeds:
        return []
    node_ids = _bounded_bfs(conn, seeds)[: max(limit, 1) * 4]
    if not node_ids:
        return []
    placeholders = ",".join("?" for _ in node_ids)
    rows = conn.execute(
        f"""SELECT id, content, node_type, domain_tags, importance FROM memories
            WHERE id IN ({placeholders}) AND forget_requested = 0
            ORDER BY importance DESC LIMIT ?""",
        (*node_ids, limit),
    ).fetchall()
    return [{"id": r[0], "content": r[1], "node_type": r[2], "domain_tags": r[3], "importance": r[4]} for r in rows]


def expire_session(conn, session_id: str, grace_days: int = 1) -> int:
    """② TTL cascade: mark all nodes owned by a deleted leader session for expiry.
    The existing pruner sweeps rows past expire_at — no new daemon."""
    ensure_graph_schema(conn)
    deadline = _now() + grace_days * 86400
    cur = conn.execute(
        "UPDATE memories SET expire_at = ? WHERE session_id = ? AND keep_forever = 0",
        (deadline, session_id),
    )
    conn.commit()
    return cur.rowcount


def sweep_expired(conn) -> int:
    """Called from the pruner: hard-delete nodes past their TTL and their edges."""
    ensure_graph_schema(conn)
    now = _now()
    ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM memories WHERE expire_at IS NOT NULL AND expire_at < ? AND keep_forever = 0",
            (now,),
        ).fetchall()
    ]
    for nid in ids:
        conn.execute("DELETE FROM graph_edges WHERE src_id = ? OR dst_id = ?", (nid, nid))
        conn.execute("DELETE FROM memories WHERE id = ?", (nid,))
    conn.commit()
    return len(ids)
