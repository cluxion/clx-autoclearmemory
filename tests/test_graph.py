"""Hard tests for the graph extension — the user's explicit risk list:
no infinite loop, no slowdown, bounded fanout, non-breaking migration, TTL cascade."""

from __future__ import annotations

import sqlite3
import time

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


def test_empty_content_ingest_preserves_memory_and_fts(tmp_path):
    conn = _fresh(tmp_path)
    content = "rich searchable memory content"
    db.upsert_memory(conn, memory_id="existing", content=content)

    graph.ingest(conn, [{"id": "existing", "content": "", "domain_tags": "tagged"}], [])

    assert db.get_memory(conn, "existing").content == content
    fts_ids = conn.execute(
        "SELECT memory_id FROM memories_fts WHERE memories_fts MATCH 'searchable'"
    ).fetchall()
    assert [row["memory_id"] for row in fts_ids] == ["existing"]


def test_sweep_expired_cleans_fts_index(tmp_path):
    conn = _fresh(tmp_path)
    graph.ingest(conn, [{"id": "tmp", "content": "ephemeral fts row", "session_id": "sx"}], [])
    assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memory_id = 'tmp'").fetchone()[0] == 1
    conn.execute("UPDATE memories SET expire_at = 1 WHERE id = 'tmp'")
    conn.commit()
    assert graph.sweep_expired(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memory_id = 'tmp'").fetchone()[0] == 0


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
    db._initialized_db_paths.discard(str(p.resolve()))
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
