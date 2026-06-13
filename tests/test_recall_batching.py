"""Recall hot-path batching: one transaction per recall_query and one
engine call per listing (score_memories)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from forgetforge import db, recall

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FORGETFORGE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "db.sqlite")
    yield connection
    connection.close()


def test_recall_query_commits_once_for_many_matches(conn) -> None:
    for index in range(5):
        db.upsert_memory(conn, memory_id=f"m-{index}", content=f"postgres tuning note {index}")
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    results = recall.recall_query(conn, "postgres")
    conn.set_trace_callback(None)
    assert len(results) == 5
    commits = [stmt for stmt in statements if stmt.strip().upper().startswith("COMMIT")]
    assert len(commits) == 1  # one fsync for the whole recall, not one per row


def test_record_retrieval_still_commits_by_default(conn) -> None:
    db.upsert_memory(conn, memory_id="m-solo", content="redis on 6380")
    conn.commit()
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    recorded = recall.record_retrieval(conn, memory_id="m-solo", layer="explicit")
    conn.set_trace_callback(None)
    assert recorded is not None
    assert any(stmt.strip().upper().startswith("COMMIT") for stmt in statements)


def test_score_memories_matches_per_row_scoring(conn) -> None:
    for index in range(4):
        db.upsert_memory(
            conn,
            memory_id=f"m-{index}",
            content=f"note {index}",
            importance=0.2 * index,
            frequency=0.1 * index,
        )
    rows = db.list_memories(conn, limit=10)
    batched = recall.score_memories(rows)
    assert [entry["memory_id"] for entry in batched] == [row.id for row in rows]
    for row, entry in zip(rows, batched, strict=True):
        single = recall.score_memory(row)
        assert entry["tier"] == single["tier"]
        assert entry["action"] == single["action"]
        assert entry["retention"] == pytest.approx(single["retention"])


def test_score_memories_pins_keep_forever_retention(conn) -> None:
    db.upsert_memory(conn, memory_id="m-keep", content="never forget")
    db.mark_keep_forever(conn, "m-keep")
    rows = [db.get_memory(conn, "m-keep")]
    batched = recall.score_memories(rows)
    assert batched[0]["retention"] == 1.0
    assert batched[0]["action"] == "keep_forever_tag"


def test_score_memories_empty_is_empty() -> None:
    assert recall.score_memories([]) == []
