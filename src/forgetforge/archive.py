from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forgetforge.config import ForgetForgeConfig

_PRIVATE_FILE_MODE = 0o600


def write_cold_archive_batch(
    cfg: ForgetForgeConfig,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Archive many cold memories in one pass.

    One parquet file and one jsonl handle per run: per-record parquet writes
    (~20ms each) dominate pruner wall-clock once demotions number in the
    hundreds. Each record needs memory_id, content, retention, tier.
    """
    if not records:
        return {"format": "noop", "parquet": None, "jsonl": None, "count": 0}
    cfg.archive_dir.mkdir(parents=True, exist_ok=True)
    archived_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    rows: list[dict[str, Any]] = []
    for record in records:
        content = str(record["content"])
        summary = content.strip().splitlines()[0][:500] if content.strip() else ""
        rows.append(
            {
                "memory_id": str(record["memory_id"]),
                "retention": float(record["retention"]),
                "tier": str(record["tier"]),
                "summary": summary,
                "archived_at": archived_at,
            }
        )
    stamp = archived_at.replace(":", "").replace("-", "").replace("+0000", "Z")
    parquet_path = cfg.archive_dir / f"cold_{stamp}.parquet"
    jsonl_path = cfg.archive_dir / "cold_archive.jsonl"
    written = "jsonl"
    old_umask = os.umask(0o077)
    try:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.Table.from_pylist(rows)
            pq.write_table(table, parquet_path)
            written = "parquet"
        except ImportError:
            parquet_path = None
        with jsonl_path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        for row in rows:
            txt_path = cfg.archive_dir / f"{row['memory_id']}.txt"
            txt_path.write_text(f"# retention={row['retention']:.3f}\n{row['summary']}\n", encoding="utf-8")
            _chmod_private(txt_path)
    finally:
        os.umask(old_umask)
    _chmod_private(jsonl_path)
    if parquet_path:
        _chmod_private(parquet_path)
    return {
        "format": written,
        "parquet": str(parquet_path) if parquet_path else None,
        "jsonl": str(jsonl_path),
        "count": len(rows),
    }


def write_cold_archive(
    cfg: ForgetForgeConfig,
    *,
    memory_id: str,
    content: str,
    retention: float,
    tier: str,
) -> dict[str, Any]:
    """Single-record archive, kept for API compatibility."""
    result = write_cold_archive_batch(
        cfg,
        [{"memory_id": memory_id, "content": content, "retention": retention, "tier": tier}],
    )
    return {"format": result["format"], "parquet": result["parquet"], "jsonl": result["jsonl"]}


__all__ = ["write_cold_archive", "write_cold_archive_batch"]


def _chmod_private(path: Path) -> None:
    if path.exists():
        path.chmod(_PRIVATE_FILE_MODE)
