"""What a chosen reserving method actually computes, once its inputs are known.

The five methods in :data:`RESERVE_METHODS` are Excel formulas, and two of them degrade
silently. ``Reported BF Ultimate = (1 − 1/Reported CDF) × EP × Implied LR + Reported Claims``
has two multiplicands that can each be zero:

* a Selected CDF of **1.0** — the placeholder `write_selected_rows` seeds, meaning no factor was
  ever chosen — makes ``(1 − 1/1)`` zero;
* a blank **Implied LR** makes ``EP × Implied LR`` zero.

With either, the formula returns ``Ultimate = Reported Claims`` and the row books no IBNR. The
arithmetic is right and the result may well be intended — "reported claims are ultimate for this
quarter" is a real actuarial position. What is not acceptable is that nothing says so: the
workbook records `Selected Method = Reported BF`, so the audit trail asserts a
Bornhuetter-Ferguson calculation that did not happen.

Measured on the client's production book (see `docs/TRIANGLE_REPORTED_AND_EP_PLAN.md` §1A/§1B):
447 of 520 booked rows carried `Reported BF`, and on one workbook 15 of 16 accident quarters had
both terms switched off. It took a database investigation to establish that this was deliberate.
These notes exist so the next reader is told instead.

This module is the single source of that judgement. `src/lib/methodNotes.ts` mirrors it for the
live preview, and `methodNotes.fixture.json` — generated from here — holds the two implementations
together, the same contract `ldfAverages` keeps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: The engine's method vocabulary, baked into the `Ultimate Claims` IF() formula. Extending it
#: rewrites every workbook, which is why "reported is ultimate" is expressed as `Reported CL`
#: with a CDF of 1.0 rather than as a sixth method.
RESERVE_METHODS = ("Paid CL", "Reported CL", "ELR", "Paid BF", "Reported BF")

BF_METHODS = ("Paid BF", "Reported BF")
#: Methods whose ultimate is a multiple of Implied LR, so a blank one zeroes a term.
LR_METHODS = ("ELR", "Paid BF", "Reported BF")

#: Money is compared at the cent, the precision the workbook presents.
_CENT = 0.005


@dataclass(frozen=True)
class MethodNote:
    code: str
    text: str
    #: "info" states a consequence; "warn" means the method is not doing what its name says.
    severity: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "text": self.text, "severity": self.severity}


def _f(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # NaN is "unset", not zero


def ultimate_for_method(
    method: str,
    *,
    paid_cdf: float = 1.0,
    reported_cdf: float = 1.0,
    implied_lr: Any = None,
    ep: float = 0.0,
    paid_claims: float = 0.0,
    os_claims: float = 0.0,
    reported_claims: float = 0.0,
    large_paid: float = 0.0,
    large_incurred: float = 0.0,
) -> float:
    """The ultimate the workbook will compute, mirroring `engine.py` exactly.

    A blank Implied LR is zero here because that is what Excel does with an empty cell — the
    point of the notes below is that the user probably did not intend it.
    """
    lr = _f(implied_lr) or 0.0
    if method == "Paid CL":
        return (paid_claims - large_paid) * paid_cdf + large_incurred
    if method == "Reported CL":
        return (reported_claims - large_incurred) * reported_cdf + large_incurred
    if method == "ELR":
        return lr * ep
    if method == "Paid BF":
        return (1 - 1 / paid_cdf) * ep * lr + os_claims if paid_cdf else 0.0
    if method == "Reported BF":
        return (1 - 1 / reported_cdf) * ep * lr + reported_claims if reported_cdf else 0.0
    return 0.0


def notes_for_row(
    method: str | None,
    *,
    paid_cdf: float = 1.0,
    reported_cdf: float = 1.0,
    implied_lr: Any = None,
    ep: float = 0.0,
    paid_claims: float = 0.0,
    os_claims: float = 0.0,
    reported_claims: float = 0.0,
    large_paid: float = 0.0,
    large_incurred: float = 0.0,
) -> list[MethodNote]:
    """Consequences of this row's own numbers, most specific first.

    Never blocks: a zero development term can be exactly what the actuary means. It is stated,
    with what it produces, so the choice is visible rather than inferred.
    """
    if not method or method not in RESERVE_METHODS:
        return []

    notes: list[MethodNote] = []
    lr = _f(implied_lr)
    cdf = reported_cdf if method == "Reported BF" else paid_cdf
    basis = "Reported" if method == "Reported BF" else "Paid"

    development_dead = method in BF_METHODS and cdf is not None and abs(cdf - 1.0) < 1e-12

    # The expected-loss term is EP × Implied LR, so EITHER factor being zero kills it. An
    # earlier version of this check looked only at the loss ratio and stayed silent when the
    # earned premium was zero — which is not hypothetical: the reserve workbook fills EP with
    # zero for every accident period outside the run's BOOKING window, so on a run whose
    # experience period is wider than its booking period those rows return reported claims
    # unchanged with every input looking healthy. That is the same silent zero this module was
    # written for, reached by a different route.
    lr_missing = lr is None or lr == 0.0
    ep_zero = abs(ep) < _CENT
    expected_loss_dead = method in LR_METHODS and (lr_missing or ep_zero)

    if development_dead:
        notes.append(MethodNote(
            "bf_development_term_zero",
            f"{basis} CDF is 1.00, so the development term (1 − 1/CDF) is zero. No factor has "
            f"been selected from the {basis.lower()} triangle.",
            "warn",
        ))
    if expected_loss_dead:
        if ep_zero and lr_missing:
            cause = (
                "Earned premium is zero for this period and Implied LR "
                + ("is blank" if lr is None else "is zero")
            )
        elif ep_zero:
            cause = (
                "Earned premium is zero for this period — a period outside the run's booking "
                "window carries none"
            )
        else:
            cause = "Implied LR " + ("is blank" if lr is None else "is zero")
        notes.append(MethodNote(
            "expected_loss_term_zero",
            f"{cause}, so the expected-loss term (EP × Implied LR) is zero.",
            "warn",
        ))

    ultimate = ultimate_for_method(
        method, paid_cdf=paid_cdf, reported_cdf=reported_cdf, implied_lr=implied_lr, ep=ep,
        paid_claims=paid_claims, os_claims=os_claims, reported_claims=reported_claims,
        large_paid=large_paid, large_incurred=large_incurred,
    )

    if method in BF_METHODS and development_dead and expected_loss_dead:
        notes.append(MethodNote(
            "collapsed_to_identity",
            f"Both terms are zero, so this returns reported claims unchanged. If that is the "
            f"intention, 'Reported CL' with a CDF of 1.00 states it directly.",
            "warn",
        ))

    if method == "ELR" and expected_loss_dead:
        notes.append(MethodNote(
            "elr_ultimate_zero",
            "With no Implied LR the ELR ultimate is zero, not merely undeveloped.",
            "warn",
        ))
    elif abs(ultimate - reported_claims) < _CENT:
        notes.append(MethodNote(
            "no_ibnr",
            "Ultimate equals reported claims, so this row books no IBNR.",
            "info",
        ))

    return notes


def notes_payload(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
    """`notes_for_row` as plain dicts, for API responses and the workbook writer."""
    return [n.to_dict() for n in notes_for_row(*args, **kwargs)]
