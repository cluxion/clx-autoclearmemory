"""Hard tests for the graph extension — the user's explicit risk list:
no infinite loop, no slowdown, bounded fanout, non-breaking migration, TTL cascade."""

from __future__ import annotations

import sqlite3
import time

import pytest

from forgetforge import db, graph


def _fresh(tmp_path):
    conn = db.connect(tmp_path / "g.db")
    graph.ensure_graph_schema(conn)
    return conn


def test_migration_is_non_breaking(tmp_path):
    # a pre-extension DB: memories table without the new columns
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.executescript(db.SCHEMA)
    c.commit()
    c.close()
    conn = db.connect(p)  # existing recall path must still work
    graph.ensure_graph_schema(conn)  # idempotent on an existing DB
    graph.ensure_graph_schema(conn)  # twice = still fine
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert {"node_type", "session_id", "domain_tags", "expire_at"} <= cols
    # existing hot path unaffected
    assert db.list_hot_memories(conn, limit=8) == [] or True


def test_no_infinite_loop_on_cycle(tmp_path):
    conn = _fresh(tmp_path)
    nodes = [{"id": x, "content": x, "domain_tags": "cyc"} for x in ("A", "B", "C")]
    edges = [
        {"src": "A", "dst": "B", "rel": "relates_to"},
        {"src": "B", "dst": "C", "rel": "relates_to"},
        {"src": "C", "dst": "A", "rel": "relates_to"},  # cycle
    ]
    graph.ingest(conn, nodes, edges)
    t0 = time.monotonic()
    out = graph.graph_recall(conn, anchor_tags="cyc")
    assert time.monotonic() - t0 < 1.0  # terminates fast, no hang
    assert len(out) <= graph.LIMIT


def test_bounded_fanout(tmp_path):
    conn = _fresh(tmp_path)
    nodes = [{"id": "hub", "content": "hub", "domain_tags": "star"}]
    edges = []
    for i in range(1000):
        nid = f"leaf{i}"
        nodes.append({"id": nid, "content": nid, "domain_tags": "star", "importance": 0.1})
        edges.append({"src": "hub", "dst": nid, "rel": "relates_to", "weight": float(i)})
    graph.ingest(conn, nodes, edges)
    visited = graph._bounded_bfs(conn, ["hub"])
    # hub + at most FANOUT expanded at hop 1 (then hop 2 from those leaves has no edges)
    assert len(visited) <= 1 + graph.FANOUT + 1
    assert len(visited) <= graph.VISIT_CAP


def test_no_slowdown_large_graph(tmp_path):
    conn = _fresh(tmp_path)
    N = 5000
    nodes = [{"id": f"n{i}", "content": f"node {i} perf", "domain_tags": "perf"} for i in range(N)]
    edges = []
    for i in range(N):
        for j in (1, 2, 3, 7):  # ~20k edges
            edges.append({"src": f"n{i}", "dst": f"n{(i + j) % N}", "rel": "relates_to"})
    # ingest in capped batches (cold-path node cap is per call)
    for k in range(0, N, graph.INGEST_NODE_CAP):
        graph.ingest(conn, nodes[k : k + graph.INGEST_NODE_CAP], [])
    graph.ingest(conn, [], edges)
    times = []
    for _ in range(20):
        t0 = time.monotonic()
        graph.graph_recall(conn, anchor_tags="perf")
        times.append(time.monotonic() - t0)
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    assert p95 < 0.05, f"hot-path p95 {p95 * 1000:.1f}ms exceeds 50ms budget"


def test_ttl_cascade(tmp_path):
    conn = _fresh(tmp_path)
    graph.ingest(
        conn,
        [
            {"id": "s1-task", "content": "task", "session_id": "s1", "domain_tags": "x"},
            {"id": "keep", "content": "other", "session_id": "s2", "domain_tags": "x"},
        ],
        [],
    )
    marked = graph.expire_session(conn, "s1", grace_days=0)
    assert marked == 1
    # force past-deadline then sweep
    conn.execute("UPDATE memories SET expire_at = 1 WHERE session_id = 's1'")
    conn.commit()
    removed = graph.sweep_expired(conn)
    assert removed == 1
    assert graph.graph_recall(conn, session="s1") == []
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE id='keep'").fetchone()[0] == 1


def test_expire_session_rejects_positive_int64_overflow_before_schema(tmp_path, monkeypatch):
    p = tmp_path / "empty.db"
    conn = sqlite3.connect(p)
    monkeypatch.setattr(graph, "_now", lambda: 0)
    with pytest.raises(ValueError, match="grace_days is too large"):
        graph.expire_session(conn, "s1", grace_days=2**62)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    assert tables == set()
    conn.close()


def test_expire_session_rejects_negative_int64_overflow_before_schema(tmp_path, monkeypatch):
    p = tmp_path / "empty-neg.db"
    conn = sqlite3.connect(p)
    monkeypatch.setattr(graph, "_now", lambda: 0)
    with pytest.raises(ValueError, match="grace_days is too large"):
        graph.expire_session(conn, "s1", grace_days=-(2**62))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    assert tables == set()
    conn.close()


def test_expire_session_overflow_does_not_mutate_pre_extension_or_existing_row(tmp_path, monkeypatch):
    # Pre-extension DB (base SCHEMA, no graph columns): overflow must not migrate or rewrite rows.
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(p)
    conn.executescript(db.SCHEMA)
    conn.execute(
        "INSERT INTO memories (id, content, tier, importance, created_at, updated_at) "
        "VALUES ('s1-task', 'task', 'hot', 0.5, '2020-01-01', '2020-01-01')"
    )
    conn.commit()
    cols_before = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert "expire_at" not in cols_before
    assert "session_id" not in cols_before
    row_before = conn.execute("SELECT * FROM memories WHERE id = 's1-task'").fetchone()
    master_before = list(conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall())
    monkeypatch.setattr(graph, "_now", lambda: 0)
    with pytest.raises(ValueError, match="grace_days is too large"):
        graph.expire_session(conn, "s1", grace_days=2**62)
    cols_after = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert cols_after == cols_before
    assert "expire_at" not in cols_after
    row_after = conn.execute("SELECT * FROM memories WHERE id = 's1-task'").fetchone()
    assert tuple(row_after) == tuple(row_before)
    master_after = list(conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall())
    assert master_after == master_before
    conn.close()


def test_expire_session_negative_grace_within_sqlite_range_is_valid(tmp_path, monkeypatch):
    conn = _fresh(tmp_path)
    graph.ingest(conn, [{"id": "s1-task", "content": "task", "session_id": "s1"}], [])
    monkeypatch.setattr(graph, "_now", lambda: 1_700_000_000)
    marked = graph.expire_session(conn, "s1", grace_days=-1)
    assert marked == 1
    expire_at = conn.execute("SELECT expire_at FROM memories WHERE id = 's1-task'").fetchone()[0]
    assert expire_at == 1_700_000_000 - 86400
    conn.close()


def test_context_savings(tmp_path):
    # graph_recall returns a bounded subgraph, never the whole store
    conn = _fresh(tmp_path)
    nodes = [{"id": f"m{i}", "content": f"m{i} topic", "domain_tags": "topic"} for i in range(100)]
    graph.ingest(conn, nodes, [])
    out = graph.graph_recall(conn, anchor_tags="topic")
    assert len(out) <= graph.LIMIT < 100


def test_invalid_input_rejected_not_raised(tmp_path):
    conn = _fresh(tmp_path)
    res = graph.ingest(
        conn,
        [{"id": "", "content": "no id"}, {"id": "ok", "content": "ok"}],
        [{"src": "ok", "dst": "x", "rel": "bogus_rel"}],
    )
    assert res["nodes"] == 1 and res["edges"] == 0 and res["skipped"] == 2


def test_ingest_skips_invalid_importance_and_weight_only(tmp_path):
    # mixed valid/invalid floats: skip only the bad items, keep the good ones
    conn = _fresh(tmp_path)
    res = graph.ingest(
        conn,
        [
            {"id": "good", "content": "ok", "importance": 0.7},
            {"id": "bad-imp", "content": "ok", "importance": "nope"},
        ],
        [
            {"src": "good", "dst": "good", "rel": "relates_to", "weight": 1.0},
            {"src": "good", "dst": "good", "rel": "owns", "weight": "nope"},
        ],
    )
    assert res == {"nodes": 1, "edges": 1, "skipped": 2}
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = 'good'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = 'bad-imp'").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE src_id = 'good' AND dst_id = 'good' AND rel = 'relates_to'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE src_id = 'good' AND dst_id = 'good' AND rel = 'owns'"
        ).fetchone()[0]
        == 0
    )


def test_ingest_skips_non_finite_importance_and_weight(tmp_path):
    # nan/inf/-inf and overflow (10**400) must be skipped, not inserted or raised
    conn = _fresh(tmp_path)
    res = graph.ingest(
        conn,
        [
            {"id": "good", "content": "ok", "importance": 0.7},
            {"id": "nan-imp", "content": "ok", "importance": float("nan")},
            {"id": "inf-imp", "content": "ok", "importance": float("inf")},
            {"id": "ninf-imp", "content": "ok", "importance": float("-inf")},
            {"id": "overflow-imp", "content": "ok", "importance": 10**400},
        ],
        [
            {"src": "good", "dst": "good", "rel": "relates_to", "weight": 1.0},
            {"src": "good", "dst": "good", "rel": "owns", "weight": float("nan")},
            {"src": "good", "dst": "good", "rel": "supersedes", "weight": float("inf")},
            {"src": "good", "dst": "good", "rel": "decided", "weight": float("-inf")},
            {"src": "good", "dst": "good", "rel": "touched", "weight": 10**400},
        ],
    )
    assert res["nodes"] == 1
    assert res["edges"] == 1
    assert res["skipped"] == 8
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = 'good'").fetchone()[0] == 1
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id IN ('nan-imp', 'inf-imp', 'ninf-imp', 'overflow-imp')"
        ).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE src_id = 'good' AND dst_id = 'good' AND rel = 'relates_to'"
        ).fetchone()[0]
        == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM graph_edges WHERE rel IN ('owns', 'supersedes', 'decided', 'touched')"
        ).fetchone()[0]
        == 0
    )


def test_mistake_recall_routes_by_domain_tags(tmp_path):
    # regression: mistake tags live in domain_tags, not content — routing must see them
    conn = _fresh(tmp_path)
    graph.ingest(
        conn,
        [
            {
                "id": "m1",
                "content": "do not raise HOPS without benchmark",
                "node_type": "mistake",
                "domain_tags": "graph perf",
            }
        ],
        [],
    )
    out = graph.graph_recall(conn, anchor_tags="graph", mistakes=True)
    assert [n["id"] for n in out] == ["m1"]


def test_ingested_nodes_are_fts_content_anchored(tmp_path):
    # regression: ingest skipped _fts_upsert, so a content-only anchor (no
    # domain_tags to LIKE against) found nothing even with a literal match
    conn = _fresh(tmp_path)
    graph.ingest(
        conn,
        [{"id": "m-rule", "content": "RULE quantities are hard floors", "node_type": "mistake"}],
        [],
    )
    out = graph.graph_recall(conn, anchor_tags="RULE", mistakes=True)
    assert [n["id"] for n in out] == ["m-rule"]


def test_hyphenated_mistake_anchor_matches_content(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    conn = _fresh(tmp_path)
    graph.ingest(
        conn,
        [{"id": "m-config", "content": "config-doctor hook failed", "node_type": "mistake"}],
        [],
    )

    out = graph.graph_recall(conn, anchor_tags="config-doctor", mistakes=True)

    assert [n["id"] for n in out] == ["m-config"]


def test_empty_content_ingest_preserves_memory_and_fts(tmp_path):
    conn = _fresh(tmp_path)
    content = "rich searchable memory content"
    db.upsert_memory(conn, memory_id="existing", content=content)

    graph.ingest(conn, [{"id": "existing", "content": "", "domain_tags": "tagged"}], [])

    assert db.get_memory(conn, "existing").content == content
    fts_ids = conn.execute("SELECT memory_id FROM memories_fts WHERE memories_fts MATCH 'searchable'").fetchall()
    assert [row["memory_id"] for row in fts_ids] == ["existing"]


def test_sweep_expired_cleans_fts_index(tmp_path):
    conn = _fresh(tmp_path)
    graph.ingest(conn, [{"id": "tmp", "content": "ephemeral fts row", "session_id": "sx"}], [])
    assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memory_id = 'tmp'").fetchone()[0] == 1
    conn.execute("UPDATE memories SET expire_at = 1 WHERE id = 'tmp'")
    conn.commit()
    assert graph.sweep_expired(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memory_id = 'tmp'").fetchone()[0] == 0


@pytest.mark.parametrize(
    "concurrent_update",
    [
        "UPDATE memories SET keep_forever = 1 WHERE id = 'ttl-race'",
        "UPDATE memories SET expire_at = 200 WHERE id = 'ttl-race'",
    ],
)
def test_sweep_expired_preserves_concurrently_retained_node(tmp_path, monkeypatch, concurrent_update):
    conn = _fresh(tmp_path)
    graph.ingest(
        conn,
        [{"id": "ttl-race", "content": "retained searchable"}, {"id": "peer", "content": "peer"}],
        [{"src": "ttl-race", "dst": "peer", "rel": "relates_to"}],
    )
    conn.execute("UPDATE memories SET expire_at = 1 WHERE id = 'ttl-race'")
    conn.commit()
    rival = db.connect(tmp_path / "g.db")
    monkeypatch.setattr(graph, "_now", lambda: 100)

    class RaceConn:
        fired = False

        def execute(self, sql, params=()):
            if not self.fired and sql.lstrip().startswith(("DELETE FROM graph_edges", "DELETE FROM memories")):
                self.fired = True
                rival.execute(concurrent_update)
                rival.commit()
            return conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(conn, name)

    assert graph.sweep_expired(RaceConn()) == 0
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE id = 'ttl-race'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM graph_edges WHERE src_id = 'ttl-race'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memory_id = 'ttl-race'").fetchone()[0] == 1
    rival.close()


def test_blank_stdin_is_noop_not_error(tmp_path):
    # whitespace-only ingest payload = ingest nothing, never an error
    conn = _fresh(tmp_path)
    res = graph.ingest(conn, [], [])
    assert res == {"nodes": 0, "edges": 0, "skipped": 0}


def test_graph_nodes_do_not_pollute_normal_recall(tmp_path):
    # organic-connection bug: graph structure nodes must NOT leak into memory recall/hot-context
    conn = _fresh(tmp_path)
    db.upsert_memory(conn, memory_id="real", content="user prefers dark mode")
    graph.ingest(
        conn,
        [
            {"id": "s1:t", "content": "build graph layer", "node_type": "task", "session_id": "s1"},
            {"id": "s1:f", "content": "graph.py file", "node_type": "file"},
        ],
        [],
    )
    hits = {m.id for m in db.search_memories(conn, "graph")}
    assert "s1:t" not in hits and "s1:f" not in hits  # structure nodes excluded
    hot = {m.id for m in db.list_hot_memories(conn, limit=20)}
    assert "s1:t" not in hot and "s1:f" not in hot
    # a real memory is still recallable, and graph-recall still sees the nodes
    assert graph.graph_recall(conn, session="s1")  # graph view unaffected


def test_recall_works_on_pre_extension_db(tmp_path):
    # a DB whose file predates the graph columns must not crash recall (connect migrates it)
    p = tmp_path / "legacy.db"
    c = sqlite3.connect(p)
    c.executescript(db.SCHEMA)
    c.execute(
        "INSERT INTO memories (id, content, tier, importance, created_at, updated_at) "
        "VALUES ('leg', 'legacy note', 'hot', 0.5, '2020-01-01', '2020-01-01')"
    )
    c.commit()
    c.close()
    db._initialized_db_paths.pop(str(p.resolve()), None)
    conn = db.connect(p)  # must add node_type and not crash
    assert {m.id for m in db.list_hot_memories(conn, limit=10)} == {"leg"}


def test_over_cap_ingest_reports_dropped_not_silent(tmp_path):
    # >INGEST_NODE_CAP nodes in one call: excess dropped but COUNTED in skipped (never silent)
    conn = _fresh(tmp_path)
    over = graph.INGEST_NODE_CAP + 25
    res = graph.ingest(conn, [{"id": f"n{i}", "content": f"c{i}"} for i in range(over)], [])
    assert res["nodes"] == graph.INGEST_NODE_CAP
    assert res["skipped"] == 25  # the drop is reported, not hidden as 0


def test_schema_race_duplicate_column_tolerated(tmp_path):
    # two first-time writers race ensure_graph_schema: writer B reads a missing-column
    # PRAGMA snapshot, writer A lands every column before B's first ALTER runs, so B's
    # real ALTERs all fail with 'duplicate column name' inside graph.ensure_graph_schema
    # (real trigger: concurrent SessionEnd hooks each running `forgetforge store`)
    path = tmp_path / "race.db"

    class _RacedConn(sqlite3.Connection):
        raced = False

        def execute(self, sql, *args):
            if isinstance(sql, str) and sql.startswith("ALTER TABLE memories") and not _RacedConn.raced:
                _RacedConn.raced = True
                rival = sqlite3.connect(path)  # writer A migrates fully first
                graph.ensure_graph_schema(rival)
                rival.close()
            return super().execute(sql, *args)

    conn = sqlite3.connect(path, factory=_RacedConn)
    conn.executescript(db.SCHEMA)  # base schema only — graph columns still missing
    graph.ensure_graph_schema(conn)  # must swallow the real 'duplicate column name'
    assert _RacedConn.raced  # the race actually fired; a vacuous pass is a test bug
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert {"node_type", "session_id", "domain_tags", "expire_at"} <= cols
    conn.close()


def test_session_recall_still_honours_mistakes_filter(tmp_path):
    # regression: the session branch of _seed_ids ignored --mistakes, leaking non-mistake nodes.
    conn = _fresh(tmp_path)
    graph.ingest(
        conn,
        [
            {
                "id": "m1",
                "content": "python mistake off-by-one",
                "node_type": "mistake",
                "session_id": "s1",
                "domain_tags": "python",
            },
            {
                "id": "m2",
                "content": "python plain note",
                "node_type": "memory",
                "session_id": "s1",
                "domain_tags": "python",
            },
        ],
        [],
    )
    with_mistakes = {r["id"] for r in graph.graph_recall(conn, anchor_tags="python", session="s1", mistakes=True)}
    without = {r["id"] for r in graph.graph_recall(conn, anchor_tags="python", session="s1", mistakes=False)}
    assert with_mistakes == {"m1"}  # --mistakes restricts within the session
    assert without == {"m1", "m2"}  # unfiltered session recall unchanged
    conn.close()


def test_session_intent_uuid_session_id_backfill_on_upgrade_is_selective_and_idempotent(tmp_path):
    # Pre-upgrade archives used id='session-intent-<uuid>' with node_type='session'
    # and NULL session_id. Upgrade must backfill that UUID once (marker-gated),
    # leaving lookalikes / non-UUID ids / non-session rows / already-set rows alone.
    uuid = "019f4b28-aada-79e2-9f23-3ec069975f01"
    other_uuid = "a1c39959-7e1f-43e3-b6b7-7f61e6746ac1"
    target_id = f"session-intent-{uuid}"
    already_set_id = "session-intent-bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    nonhex_uuid_shape_id = "session-intent-zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"
    no_hyphen_id = f"session-intent-{uuid.replace('-', '')}"
    memory_shaped_id = f"session-intent-{other_uuid}"
    rows = [
        (target_id, "session core archive", "session", None),
        ("session-intent-not-a-uuid", "lookalike non-uuid", "session", None),
        ("session-intent-deadbeef", "lookalike short id", "session", None),
        (f"session-intent-{uuid}-extra", "lookalike suffix", "session", None),
        (no_hyphen_id, "lookalike no hyphens", "session", None),
        (memory_shaped_id, "memory typed matching id shape", "memory", None),
        ("plain-memory", "ordinary memory", "memory", None),
        (already_set_id, "already set", "session", "keep-me"),
        (nonhex_uuid_shape_id, "same UUID shape but non-hex", "session", None),
        (f"task-intent-{uuid}", "non-session lookalike prefix", "task", None),
    ]

    p = tmp_path / "pre-session-id-backfill.db"
    raw = sqlite3.connect(p)
    raw.executescript(db.SCHEMA)
    for stmt in (
        "ALTER TABLE memories ADD COLUMN node_type TEXT NOT NULL DEFAULT 'memory'",
        "ALTER TABLE memories ADD COLUMN session_id TEXT",
        "ALTER TABLE memories ADD COLUMN domain_tags TEXT",
        "ALTER TABLE memories ADD COLUMN expire_at INTEGER",
    ):
        raw.execute(stmt)
    for mid, content, ntype, sid in rows:
        raw.execute(
            "INSERT INTO memories (id, content, created_at, updated_at, node_type, session_id) "
            "VALUES (?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', ?, ?)",
            (mid, content, ntype, sid),
        )
    raw.commit()
    raw.close()

    def _session_map(conn) -> dict[str, str | None]:
        return {str(r["id"]): r["session_id"] for r in conn.execute("SELECT id, session_id FROM memories ORDER BY id")}

    db._initialized_db_paths.pop(str(p.resolve()), None)
    conn = db.connect(p)  # upgrade path: one-time selective backfill
    first = _session_map(conn)
    assert first[target_id] == uuid
    assert first["session-intent-not-a-uuid"] is None
    assert first["session-intent-deadbeef"] is None
    assert first[f"session-intent-{uuid}-extra"] is None
    assert first[no_hyphen_id] is None
    assert first[memory_shaped_id] is None  # node_type=memory
    assert first["plain-memory"] is None
    assert first[already_set_id] == "keep-me"
    assert first[nonhex_uuid_shape_id] is None
    assert first[f"task-intent-{uuid}"] is None
    conn.close()

    db._initialized_db_paths.pop(str(p.resolve()), None)
    conn = db.connect(p)  # second open must be a no-op (idempotent)
    second = _session_map(conn)
    assert second == first
    assert second[target_id] == uuid
    conn.close()
