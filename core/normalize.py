"""Canonicalisation and near-match scoring for reserving-class reconciliation (WP0).

The reserving engine joins premium to claims by **exact string equality** on
``RESERVINGCLASS``. Nothing raises when a value fails to match: claims whose class is absent
from the premium file are dropped, and premium classes absent from the claims files produce a
workbook full of zeros. On the reference book that silently discards **3,044 paid rows worth
35,503,674** — the whole of ``Health``, because the premium file spells it ``Health Insurance``.

This module is the matching vocabulary. It is used for COMPARISON ONLY: the original strings
are preserved for display, filenames and workbook contents, so output is unchanged for data
that is already consistent.

Two levels:

* :func:`canonical_key` absorbs the differences that are never meaningful — case, whitespace,
  punctuation. A pair that agrees here needs no alias and no human.
* :func:`match_score` ranks the rest, so the UI can propose ``Health -> Health Insurance``
  rather than leaving a user to spot it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

#: Words that qualify an insurance class without identifying it. `Health` and
#: `Health Insurance` are the same class; `Marine` and `Motor` are not. Dropping these before
#: comparison is what separates the two situations.
QUALIFIER_WORDS = frozenset(
    {
        "insurance",
        "assurance",
        "cover",
        "coverage",
        "policy",
        "policies",
        "class",
        "business",
        "line",
        "lob",
    }
)

#: Apostrophes are elisions INSIDE a word, so they are deleted rather than treated as a break:
#: `Banker's Blanket` and `Bankers Blanket` are one class, and splitting on the apostrophe
#: would give `banker s blanket` and `bankers blanket`, which do not match.
_ELISION = re.compile(r"[\u0027\u2018\u2019\u00b4`]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def canonical_key(value: object) -> str:
    """Case-, whitespace- and punctuation-insensitive match key.

    ``"  Health   Insurance. "`` and ``"health insurance"`` share a key, so a difference of
    that kind never reaches a human. ``Health`` and ``Health Insurance`` do NOT — they are a
    genuine naming difference and need an alias.
    """
    if value is None:
        return ""
    text = _ELISION.sub("", str(value).casefold())
    text = _NON_ALNUM.sub(" ", text)
    return " ".join(text.split())


def tokens(value: object) -> tuple[str, ...]:
    key = canonical_key(value)
    return tuple(key.split()) if key else ()


def core_tokens(value: object) -> tuple[str, ...]:
    """Identifying words only — qualifiers removed.

    Falls back to the full token tuple when a name is *entirely* qualifiers, so
    ``"Insurance"`` does not collapse to nothing and match everything.
    """
    all_tokens = tokens(value)
    core = tuple(t for t in all_tokens if t not in QUALIFIER_WORDS)
    return core or all_tokens


@dataclass(frozen=True)
class MatchSuggestion:
    """A proposed pairing between an unmatched value and a known one."""

    value: str
    candidate: str
    score: float
    #: Why it was proposed, shown to the user — the reason carries more than the number.
    basis: str


#: At or above this, a pairing is offered to the user. Never applied automatically: an alias
#: changes which claims enter a reserve, which is an actuarial decision.
SUGGESTION_THRESHOLD = 0.80


def match_score(a: object, b: object) -> tuple[float, str]:
    """Score a candidate pairing in ``[0, 1]``, with the reason.

    Plain sequence similarity is not usable here and this was measured, not assumed:
    ``difflib.SequenceMatcher("health", "health insurance").ratio()`` is **0.545**, so the one
    pairing this feature exists to surface would be missed at any sensible threshold. Insurance
    class names differ overwhelmingly by a qualifier — one name is the other plus a word — so
    the tiers below lead with that shape and keep sequence similarity as a last resort.
    """
    key_a, key_b = canonical_key(a), canonical_key(b)
    if not key_a or not key_b:
        return 0.0, "empty"
    if key_a == key_b:
        return 1.0, "identical apart from case or punctuation"

    core_a, core_b = core_tokens(a), core_tokens(b)
    if core_a == core_b:
        return 1.0, "same name, differing only by a qualifier such as 'Insurance'"

    set_a, set_b = set(tokens(a)), set(tokens(b))
    if set_a and set_b and (set_a <= set_b or set_b <= set_a):
        shared = len(set_a & set_b) / max(len(set_a), len(set_b))
        return 0.5 + 0.5 * shared, "one name contains every word of the other"

    if set_a & set_b:
        overlap = len(set_a & set_b) / len(set_a | set_b)
        if overlap >= 0.5:
            return 0.5 + 0.3 * overlap, "shares most of its words"

    return difflib.SequenceMatcher(None, key_a, key_b).ratio() * 0.7, "similar spelling"


def suggest_matches(
    value: object,
    candidates: object,
    *,
    threshold: float = SUGGESTION_THRESHOLD,
    limit: int = 10,
) -> list[MatchSuggestion]:
    """Best pairings for ``value`` among ``candidates``, strongest first.

    Capped, because a long list of weak guesses is worse than none — it invites a user to
    accept one without reading it.
    """
    scored: list[MatchSuggestion] = []
    for candidate in candidates:
        score, basis = match_score(value, candidate)
        if score >= threshold:
            scored.append(MatchSuggestion(str(value), str(candidate), round(score, 4), basis))
    scored.sort(key=lambda s: (-s.score, s.candidate))
    return scored[:limit]
