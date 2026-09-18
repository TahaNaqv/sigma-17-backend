"""A method that has collapsed to an identity must say so.

The case these pin is real: on the client's `Fire Payment GROSS 2024-12` workbook, fifteen of
sixteen accident quarters carried `Selected Method = Reported BF` with `Reported CDF = 1.0` and
a blank `Implied LR`, so both terms of the BF formula were zero and every one of those rows
booked no IBNR. That was intended — but nothing in the product said it, and establishing the
intent took a database investigation.
"""

import pytest

from module1_engine.method_notes import (
    RESERVE_METHODS,
    notes_for_row,
    ultimate_for_method,
)

# 2021-Q1 of the client's Fire Payment GROSS 2024-12 workbook, verbatim.
FIRE_2021Q1 = dict(
    paid_cdf=1.0, reported_cdf=1.0, implied_lr=None,
    ep=141243571.0, paid_claims=27260563.0, os_claims=784956.0,
    reported_claims=28045519.0,
)


def codes(*args, **kwargs):
    return [n.code for n in notes_for_row(*args, **kwargs)]


def test_a_healthy_row_says_nothing():
    assert codes(
        "Reported BF", reported_cdf=1.6, implied_lr=0.62, ep=100_000.0,
        reported_claims=40_000.0,
    ) == []


def test_the_production_row_reports_both_dead_terms_and_the_consequence():
    assert codes("Reported BF", **FIRE_2021Q1) == [
        "bf_development_term_zero",
        "expected_loss_term_zero",
        "collapsed_to_identity",
        "no_ibnr",
    ]


def test_the_collapsed_row_returns_reported_claims_exactly():
    assert ultimate_for_method("Reported BF", **FIRE_2021Q1) == pytest.approx(
        FIRE_2021Q1["reported_claims"]
    )


def test_a_cdf_of_one_alone_is_flagged_without_the_identity_note():
    out = codes("Reported BF", reported_cdf=1.0, implied_lr=0.62, ep=100_000.0,
                reported_claims=40_000.0)
    assert out == ["bf_development_term_zero", "no_ibnr"]


def test_a_blank_lr_alone_is_flagged_without_the_identity_note():
    out = codes("Reported BF", reported_cdf=1.6, implied_lr=None, ep=100_000.0,
                reported_claims=40_000.0)
    assert out == ["expected_loss_term_zero", "no_ibnr"]


def test_zero_is_treated_as_unset_for_implied_lr():
    """An explicit 0.0 loss ratio produces the same dead term as a blank one."""
    assert "expected_loss_term_zero" in codes(
        "Reported BF", reported_cdf=1.6, implied_lr=0.0, ep=100_000.0, reported_claims=40_000.0
    )


def test_zero_earned_premium_kills_the_expected_loss_term_even_with_a_loss_ratio_set():
    """The blind spot this check originally had.

    `EP × Implied LR` needs both factors. The reserve workbook fills EP with zero for every
    accident period outside the run's BOOKING window (`engine.py`: "additional_summaries only
    has GEP/RI_EP for quarters between BOP and EOP; fill missing with 0"), so on a run whose
    experience period is wider than its booking period — production job `3cb6aba9` is exactly
    that — those rows return reported claims unchanged while every visible input looks healthy.
    Checking only the loss ratio stayed silent through it.
    """
    out = codes("Reported BF", reported_cdf=1.6, implied_lr=0.62, ep=0.0,
                reported_claims=40_000.0)
    assert out == ["expected_loss_term_zero", "no_ibnr"]
    assert ultimate_for_method(
        "Reported BF", reported_cdf=1.6, implied_lr=0.62, ep=0.0, reported_claims=40_000.0
    ) == pytest.approx(40_000.0)


def test_the_note_names_which_factor_is_zero():
    """Naming the cause is the whole job: "set an Implied LR" and "this period is outside the
    booking window" are different problems with different fixes."""
    ep_zero = notes_for_row("Reported BF", reported_cdf=1.6, implied_lr=0.62, ep=0.0,
                            reported_claims=40_000.0)[0].text
    assert "Earned premium is zero" in ep_zero
    assert "booking window" in ep_zero

    lr_blank = notes_for_row("Reported BF", reported_cdf=1.6, implied_lr=None, ep=100_000.0,
                             reported_claims=40_000.0)[0].text
    assert "Implied LR is blank" in lr_blank
    assert "Earned premium" not in lr_blank

    both = notes_for_row("Reported BF", reported_cdf=1.6, implied_lr=None, ep=0.0,
                         reported_claims=40_000.0)[0].text
    assert "Earned premium is zero" in both and "Implied LR is blank" in both


def test_elr_without_a_loss_ratio_is_zero_not_merely_undeveloped():
    out = codes("ELR", implied_lr=None, ep=100_000.0, reported_claims=40_000.0)
    assert out == ["expected_loss_term_zero", "elr_ultimate_zero"]
    assert ultimate_for_method("ELR", implied_lr=None, ep=100_000.0) == 0.0


def test_paid_bf_is_judged_on_the_paid_cdf_not_the_reported_one():
    out = codes("Paid BF", paid_cdf=1.0, reported_cdf=9.9, implied_lr=0.62,
                ep=100_000.0, os_claims=5_000.0, reported_claims=40_000.0)
    assert "bf_development_term_zero" in out


def test_chain_ladder_methods_carry_no_bf_notes():
    for method in ("Paid CL", "Reported CL"):
        out = codes(method, paid_cdf=1.0, reported_cdf=1.0, implied_lr=None,
                    paid_claims=40_000.0, reported_claims=40_000.0)
        assert "bf_development_term_zero" not in out
        assert "expected_loss_term_zero" not in out


def test_reported_cl_at_cdf_one_is_the_sanctioned_way_to_say_reported_is_ultimate():
    """No sixth method: `RESERVE_METHODS` is baked into the workbook formula."""
    assert ultimate_for_method("Reported CL", reported_cdf=1.0, reported_claims=40_000.0) == 40_000.0
    assert codes("Reported CL", reported_cdf=1.0, reported_claims=40_000.0) == ["no_ibnr"]
    assert len(RESERVE_METHODS) == 5


def test_an_unknown_or_missing_method_is_silent_rather_than_wrong():
    assert codes(None) == []
    assert codes("Reported = Ultimate") == []


def test_large_claim_add_back_does_not_trip_the_identity_note():
    """With an add-back the ultimate exceeds reported claims, so no_ibnr must not fire."""
    out = codes("Reported CL", reported_cdf=1.4, reported_claims=40_000.0,
                large_incurred=5_000.0)
    assert out == []
