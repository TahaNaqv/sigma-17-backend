"""Leaving a reserving class out of a run.

The client's Health Insurance carries 13,370 premium rows and no claims in either claims file,
because that experience is simply not supplied. Left in, it earns premium into the LRC, reports a
zero loss ratio and produces a zero ultimate under every method — one side of a balance sheet with
nothing on the other. They have asked us to run without it.

The exclusion is applied at the engine's read boundary, after aliasing, so it is written in the
canonical name the user sees in the output rather than whichever spelling a given file uses.
"""

import pandas as pd
import pytest

from module1_engine.engine import apply_class_aliases, drop_reserving_classes


def _frame():
    return pd.DataFrame({
        "RESERVINGCLASS": ["Motor", "Health Insurance", "Fire", "Health Insurance"],
        "AMOUNT": [1.0, 2.0, 3.0, 4.0],
    })


def test_no_exclusion_leaves_the_frame_untouched():
    """What keeps every existing golden bit-identical."""
    frame = _frame()
    assert drop_reserving_classes(frame, None) is frame
    assert drop_reserving_classes(frame, []) is frame
    assert drop_reserving_classes(frame, ()) is frame


def test_an_excluded_class_is_removed_entirely():
    out = drop_reserving_classes(_frame(), ["Health Insurance"])
    assert list(out["RESERVINGCLASS"]) == ["Motor", "Fire"]
    assert out["AMOUNT"].sum() == 4.0


def test_matching_absorbs_case_and_punctuation():
    """Same canonical-key rule as aliasing, so an exclusion cannot be defeated by a spelling."""
    for spelling in ("health insurance", "HEALTH INSURANCE", " Health  Insurance "):
        out = drop_reserving_classes(_frame(), [spelling])
        assert "Health Insurance" not in list(out["RESERVINGCLASS"]), spelling


def test_a_class_that_is_not_present_changes_nothing():
    frame = _frame()
    assert drop_reserving_classes(frame, ["Aviation"]) is frame


def test_exclusion_is_applied_after_aliasing_so_it_names_the_canonical_class():
    """A claims file spelling the class 'Health' is aliased to 'Health Insurance' first, and the
    exclusion then catches it. Excluding on the raw spelling would miss whichever files use the
    other one."""
    raw = pd.DataFrame({"RESERVINGCLASS": ["Health", "Motor"], "AMOUNT": [1.0, 2.0]})
    aliased = apply_class_aliases(raw, {"Health": "Health Insurance"})
    out = drop_reserving_classes(aliased, ["Health Insurance"])
    assert list(out["RESERVINGCLASS"]) == ["Motor"]


def test_frames_without_the_column_are_tolerated():
    frame = pd.DataFrame({"OTHER": [1]})
    assert drop_reserving_classes(frame, ["Health Insurance"]) is frame
    assert drop_reserving_classes(None, ["Health Insurance"]) is None


def test_excluding_every_class_yields_an_empty_frame_rather_than_raising():
    out = drop_reserving_classes(_frame(), ["Motor", "Health Insurance", "Fire"])
    assert len(out) == 0
