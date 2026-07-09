"""Per-(line, bucket) source map for the IFRS 17 movement disclosure.

Loaded from the committed ``mapping_source.json`` (regenerate with
``scripts/gen_movement_mapping.py``). For each disclosure line it says, per measurement
bucket, where the value comes from in the Module-2 ``IFRS Summary`` frame — a direct
column (D), a derived expression (Δ), a class×cohort manual override input (O), or
manual/0 (M) — together with the direction sign the engine applies.

AUTHORITATIVE — encoded verbatim from the client's signed disclosure file (sha in the
mapping ``_meta``). Pure stdlib; safe to import anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .schema import SCHEMA

_SOURCE_PATH = Path(__file__).with_name("mapping_source.json")

#: Tiers a (line, bucket) value can take.
TIER_DIRECT = "D"  # a single direct column of IFRS Summary
TIER_DERIVED = "Δ"  # an arithmetic expression over IFRS Summary columns
TIER_OVERRIDE = "O"  # class×cohort manual override input (override Dataset, plan §7)
TIER_MANUAL = "M"  # judgment / no data source (defaults to 0 until an override fills it)
DATA_BACKED = frozenset({TIER_DIRECT, TIER_DERIVED})
OVERRIDE_FILLED = frozenset({TIER_OVERRIDE, TIER_MANUAL})


@dataclass(frozen=True)
class BucketSource:
    """The source for one (line, bucket) cell."""

    bucket: str
    sign: str | None  # "+" | "-" | "+/-" | "-/+" | None
    tier: str  # D | Δ | O | M
    source: str | None  # positive-magnitude column/expression, or None
    override_key: str | None = None  # for tier O: the override input key

    @property
    def sign_mult(self) -> float:
        """Direction multiplier the engine applies to the resolved magnitude."""
        return -1.0 if (self.sign or "").strip().startswith("-") else 1.0


@dataclass(frozen=True)
class LineMapping:
    sheet: str
    line_id: str
    label: str
    kind: str  # opening | input | subtotal | closing | section
    buckets: dict[str, BucketSource] = field(default_factory=dict)  # bucket -> source
    subtotal_formulas: dict[str, str] | None = None  # structural lines: bucket -> Excel SUM

    @property
    def has_template(self) -> bool:
        """True if any bucket source is prev/curr-templated (an opening build-up line)."""
        return any("{p}" in (b.source or "") or "{P}" in (b.source or "")
                   for b in self.buckets.values())


@lru_cache(maxsize=1)
def _load() -> dict[tuple[str, str], LineMapping]:
    raw = json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], LineMapping] = {}
    for sheet, block in raw.items():
        if sheet.startswith("_"):
            continue
        for line_id, m in block["lines"].items():
            buckets = {
                b: BucketSource(
                    bucket=b,
                    sign=bs.get("sign"),
                    tier=bs["tier"],
                    source=bs.get("source"),
                    override_key=bs.get("override_key"),
                )
                for b, bs in (m.get("buckets") or {}).items()
            }
            out[(sheet, line_id)] = LineMapping(
                sheet=sheet,
                line_id=line_id,
                label=m["label"],
                kind=m["kind"],
                buckets=buckets,
                subtotal_formulas=m.get("subtotal_formulas"),
            )
    return out


def get_mapping(sheet: str, line_id: str) -> LineMapping:
    return _load()[(sheet, line_id)]


def override_keys(sheet: str) -> list[str]:
    """Stable keys of the class×cohort override inputs used by this sheet (tier O)."""
    keys: list[str] = []
    for (s, _), m in _load().items():
        if s != sheet:
            continue
        for b in m.buckets.values():
            if b.tier == TIER_OVERRIDE and b.override_key and b.override_key not in keys:
                keys.append(b.override_key)
    return keys


def override_line_ids(sheet: str) -> list[str]:
    """Lines with any manual/override bucket — the override surface (plan §7)."""
    return [m.line_id for (s, _), m in _load().items()
            if s == sheet and any(b.tier in OVERRIDE_FILLED for b in m.buckets.values())]


def coverage() -> dict[str, dict[str, int]]:
    raw = json.loads(_SOURCE_PATH.read_text(encoding="utf-8"))
    return {s: raw[s]["coverage"] for s in raw if not s.startswith("_")}


def validate_mapping() -> list[str]:
    """Cross-check the mapping against the schema. Returns problems ([] == valid).

    Asserts: every schema line has exactly one mapping entry and vice-versa; buckets only
    appear on value ('input') lines and only on the sheet's declared value buckets; a
    D/Δ bucket carries a source expression; an O bucket carries an override_key; structural
    lines carry no bucket sources. Does NOT assert actuarial correctness of the source
    expressions or signs — the reconciliation control (compute) surfaces those.
    """
    problems: list[str] = []
    mp = _load()
    for name, sheet in SCHEMA.sheets.items():
        value_buckets = set(sheet.value_buckets)
        schema_ids = {ln.id for ln in sheet.lines}
        map_ids = {lid for (s, lid) in mp if s == name}
        if schema_ids - map_ids:
            problems.append(f"{name}: lines missing from mapping: {sorted(schema_ids - map_ids)}")
        if map_ids - schema_ids:
            problems.append(f"{name}: mapping has unknown lines: {sorted(map_ids - schema_ids)}")
        for ln in sheet.lines:
            m = mp.get((name, ln.id))
            if m is None:
                continue
            if ln.kind != "input" and m.buckets:
                problems.append(f"{name}.{ln.id}: structural line ({ln.kind}) must not carry bucket sources")
            for b, bs in m.buckets.items():
                if b not in value_buckets:
                    problems.append(f"{name}.{ln.id}: source on unknown bucket {b!r}")
                if bs.tier in DATA_BACKED and not bs.source:
                    problems.append(f"{name}.{ln.id}.{b}: tier {bs.tier} but no source expression")
                if bs.tier == TIER_OVERRIDE and not bs.override_key:
                    problems.append(f"{name}.{ln.id}.{b}: tier O but no override_key")
                if bs.tier in DATA_BACKED and bs.override_key:
                    problems.append(f"{name}.{ln.id}.{b}: data-backed tier must not have override_key")
    return problems
