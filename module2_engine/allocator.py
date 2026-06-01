"""Allocator entrypoints for Module 2."""

from __future__ import annotations

from typing import Any

from .engine import _build_allocate_outputs


def allocate_from_combined_summary(
    combined_summary_bytes: bytes, selected_ulr_rows: list[dict[str, Any]] | None = None
) -> tuple[bytes, list[dict[str, Any]]]:
    out_bytes, ulr_rows, _ = _build_allocate_outputs(combined_summary_bytes, selected_ulr_rows)
    return out_bytes, ulr_rows
