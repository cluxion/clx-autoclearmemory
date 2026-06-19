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

# Shared subject/predicate context required before any contradiction signal fires.
_MIN_CONTEXT_OVERLAP = 3
_MIN_CONTEXT_JACCARD = 0.45
# Substitution-style contradictions (e.g. bridge vs host networking) need
# very high overlap plus only a few differing content words.
_MIN_SUBSTITUTION_JACCARD = 0.55
_NEGATION_MODIFIERS = ("never", "always")


@dataclass(frozen=True)
class ContradictionHit:
    memory_id: str
    reason: str
    overlap_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "reason": self.reason,
            "overlap_score": self.overlap_score,
            "advisory": True,
        }


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-zA-Z0-9_]{3,}", text)}


def _negation_vocab() -> set[str]:
    words: set[str] = set()
    for a, b in NEGATION_PAIRS:
        words.add(a)
        for part in b.split():
            if len(part) >= 3:
                words.add(part)
    return words


def _context_tokens(tokens: set[str]) -> set[str]:
    return tokens - _negation_vocab()


def _context_overlap(new_tokens: set[str], old_tokens: set[str]) -> tuple[set[str], float]:
    new_ctx = _context_tokens(new_tokens)
    old_ctx = _context_tokens(old_tokens)
    overlap = new_ctx & old_ctx
    union = new_ctx | old_ctx
    jaccard = len(overlap) / max(len(union), 1)
    return overlap, jaccard


def _shared_context(new_tokens: set[str], old_tokens: set[str]) -> tuple[set[str], float] | None:
    overlap, jaccard = _context_overlap(new_tokens, old_tokens)
    if len(overlap) < _MIN_CONTEXT_OVERLAP or jaccard < _MIN_CONTEXT_JACCARD:
        return None
    return overlap, jaccard


def _contains_term(text: str, term: str) -> bool:
    if " " in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def _negation_pair_hit(lower_new: str, lower_old: str) -> str | None:
    for a, b in NEGATION_PAIRS:
        if (_contains_term(lower_new, a) and _contains_term(lower_old, b)) or (
            _contains_term(lower_new, b) and _contains_term(lower_old, a)
        ):
            return f"negation_pair:{a}/{b}"
    return None


def _negation_modifier_hit(lower_new: str, lower_old: str) -> str | None:
    for modifier in _NEGATION_MODIFIERS:
        new_has = _contains_term(lower_new, modifier)
        old_has = _contains_term(lower_old, modifier)
        if new_has != old_has:
            return f"negation_modifier:{modifier}"
    return None


def _substitution_hit(new_tokens: set[str], old_tokens: set[str], *, jaccard: float) -> bool:
    if jaccard < _MIN_SUBSTITUTION_JACCARD:
        return False
    diff_new = _context_tokens(new_tokens) - _context_tokens(old_tokens)
    diff_old = _context_tokens(old_tokens) - _context_tokens(new_tokens)
    return len(diff_new) == 1 and len(diff_old) == 1


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
    lower_new = content.lower()
    for row in db.list_memories(conn, limit=500):
        if exclude_id and row.id == exclude_id:
            continue
        old_tokens = _tokens(row.content)
        shared = _shared_context(new_tokens, old_tokens)
        if shared is None:
            continue
        _overlap, jaccard = shared
        lower_old = row.content.lower()
        reason = _negation_pair_hit(lower_new, lower_old)
        if reason is None:
            reason = _negation_modifier_hit(lower_new, lower_old)
        if reason is None and _substitution_hit(new_tokens, old_tokens, jaccard=jaccard):
            reason = "conflicting_claim"
        if reason is None:
            continue
        hits.append(
            ContradictionHit(
                memory_id=row.id,
                reason=reason,
                overlap_score=round(jaccard, 3),
            )
        )
        if len(hits) >= limit:
            break
    return hits


__all__ = ["ContradictionHit", "detect_contradictions"]