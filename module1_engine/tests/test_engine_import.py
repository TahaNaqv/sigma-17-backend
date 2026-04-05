"""Smoke tests for module1_engine (requires pandas, openpyxl, numpy)."""

import pytest

from module1_engine import run_generate_summary, run_policy_level_upr, run_update_reserve_summary


def test_exports_are_callable():
    assert callable(run_generate_summary)
    assert callable(run_policy_level_upr)
    assert callable(run_update_reserve_summary)


def test_select_experience_period():
    from module1_engine.engine import select_experience_period

    a, b = select_experience_period("01-01-2024", "31-12-2024")
    assert a.year == 2024
    assert b.year == 2024
