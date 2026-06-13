from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

NEGATION_PAIRS = (
    ("always", "never"),
    ("enable", "disable"),
    ("prefer", "avoid"),
    ("use", "don't use"),
    ("true", "false"),
    ("yes", "no"),
)


@dataclass(frozen=True)
class ContradictionHit:
    memory_id: str
    reason: str
    overlap_score: float

    def to_dict(self) -> dict[str, Any]:
        return {"memory_id": self.memory_id, "reason": self.reason, "overlap_score": self.overlap_score}


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-zA-Z0-9_]{3,}", text)}


def detect_contradictions(
    conn,
    *,
    content: str,
    exclude_id: str | None = None,
    limit: int = 5,
) -> list[ContradictionHit]:
    from forgetforge import db

    new_tokens = _tokens(content)
    if len(new_tokens) < 3:
        return []
    hits: list[ContradictionHit] = []
    for row in db.list_memories(conn, limit=500):
        if exclude_id and row.id == exclude_id:
            continue
        old_tokens = _tokens(row.content)
        overlap = new_tokens & old_tokens
        if len(overlap) < 2:
            continue
        score = len(overlap) / max(len(new_tokens), 1)
        lower_new = content.lower()
        lower_old = row.content.lower()
        for a, b in NEGATION_PAIRS:
            if (a in lower_new and b in lower_old) or (b in lower_new and a in lower_old):
                hits.append(
                    ContradictionHit(
                        memory_id=row.id,
                        reason=f"negation_pair:{a}/{b}",
                        overlap_score=round(score, 3),
                    )
                )
                break
        else:
            if score >= 0.35 and lower_new[:80] != lower_old[:80]:
                hits.append(
                    ContradictionHit(
                        memory_id=row.id,
                        reason="high_token_overlap",
                        overlap_score=round(score, 3),
                    )
                )
        if len(hits) >= limit:
            break
    return hits


__all__ = ["ContradictionHit", "detect_contradictions"]
