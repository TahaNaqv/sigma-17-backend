"""Proposed measure→line mapping for the IFRS 17 movement disclosure.

Loaded from the committed ``mapping_source.json`` (regenerate with
``scripts/gen_movement_mapping.py``). Says, per disclosure line, where its value
comes from in the Module-2 ``IFRS Summary`` frame.

⚠️ PROPOSED — requires actuarial sign-off before production use (plan §1, gate #1).
Pure stdlib; safe to import anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .schema import SCHEMA

_SOURCE_PATH = Path(__file__).with_name("mapping_source.json")

#: Tiers a line's value can take.
TIER_DIRECT = "D"  # direct column of IFRS Summary
TIER_DERIVED = "Δ"  # formula over IFRS Summary columns
TIER_MANUAL = "M"  # judgment / override (no data source)
TIER_RESIDUAL = "residual"  # balancing "methodology diff" line (recon residual)
DATA_BACKED = frozenset({TIER_DIRECT, TIER_DERIVED})


@dataclass(frozen=True)
class LineMapping:
    sheet: str
    line_id: str
    label: str
    kind: str
    tier: str
    source: str | None  # column name or derived expression; None for manual/structural


@lru_cache(maxsize=1)
def _load() -> dict[tuple[str, str], LineMapping]:
    raw = json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], LineMapping] = {}
    for sheet, block in raw.items():
        if sheet.startswith("_"):
            continue
        for line_id, m in block["lines"].items():
            out[(sheet, line_id)] = LineMapping(
                sheet=sheet,
                line_id=line_id,
                label=m["label"],
                kind=m["kind"],
                tier=m["tier"],
                source=m.get("source"),
            )
    return out


def get_mapping(sheet: str, line_id: str) -> LineMapping:
    return _load()[(sheet, line_id)]


def manual_line_ids(sheet: str) -> list[str]:
    """Lines with no data source — the override surface (plan §7)."""
    return [m.line_id for (s, _), m in _load().items() if s == sheet and m.tier == TIER_MANUAL]


def coverage() -> dict[str, dict[str, int]]:
    raw = json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))
    return {s: raw[s]["coverage"] for s in raw if not s.startswith("_")}


def validate_mapping() -> list[str]:
    """Cross-check the mapping against the schema. Returns problems ([] == valid).

    Asserts every schema line has exactly one mapping entry and vice-versa, and that
    each line's mapping tier is consistent with its schema kind. Note: a *subtotal*
    line MAY carry a data source — that means "use this measured value directly rather
    than summing children" (e.g. Insurance finance: the total is data-backed but its
    P&L/OCI split is manual, so summing the manual children would wrongly zero it).
    Only true section headers must never carry a source.
    Does NOT assert actuarial correctness of the source expressions (sign-off, §1).
    """
    problems: list[str] = []
    mp = _load()
    for name, sheet in SCHEMA.sheets.items():
        schema_ids = {ln.id for ln in sheet.lines}
        map_ids = {lid for (s, lid) in mp if s == name}
        missing = schema_ids - map_ids
        extra = map_ids - schema_ids
        if missing:
            problems.append(f"{name}: lines missing from mapping: {sorted(missing)}")
        if extra:
            problems.append(f"{name}: mapping has unknown lines: {sorted(extra)}")
        for ln in sheet.lines:
            m = mp.get((name, ln.id))
            if m is None:
                continue
            if ln.kind == "section" and m.source:
                problems.append(f"{name}.{ln.id}: section header must not have a source")
            if m.tier in DATA_BACKED and not m.source:
                problems.append(f"{name}.{ln.id}: tier {m.tier} but no source expression")
    return problems
