"""Generate `methodNotes.fixture.json` for the dashboard from the Python implementation.

The browser previews the method notes live as the actuary changes Implied LR or Selected
Method; the workbook writer emits the same notes. Two implementations, one meaning — so the
TypeScript is checked against cases produced *here* rather than against a second opinion.

    python scripts/gen_method_notes_fixture.py [--out <path>]

Re-run whenever `module1_engine/method_notes.py` changes; the parity test fails otherwise.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module1_engine.method_notes import notes_payload, ultimate_for_method  # noqa: E402

DEFAULT_OUT = (
    Path(__file__).resolve().parents[2]
    / "sigma-17-dashboard" / "src" / "lib" / "methodNotes.fixture.json"
)

#: Named cases, chosen to pin every branch — including the client's own production row, so a
#: regression is expressed in numbers someone has actually booked.
CASES: dict[str, dict] = {
    "healthy_reported_bf": dict(
        method="Reported BF", reported_cdf=1.6, paid_cdf=2.1, implied_lr=0.62,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "fire_2021q1_production": dict(
        method="Reported BF", reported_cdf=1.0, paid_cdf=1.0, implied_lr=None,
        ep=141_243_571.0, paid_claims=27_260_563.0, os_claims=784_956.0,
        reported_claims=28_045_519.0,
    ),
    "cdf_one_only": dict(
        method="Reported BF", reported_cdf=1.0, paid_cdf=2.1, implied_lr=0.62,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "blank_lr_only": dict(
        method="Reported BF", reported_cdf=1.6, paid_cdf=2.1, implied_lr=None,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "zero_lr_is_unset": dict(
        method="Reported BF", reported_cdf=1.6, paid_cdf=2.1, implied_lr=0.0,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "ep_zero_outside_booking_window": dict(
        method="Reported BF", reported_cdf=1.6, paid_cdf=2.1, implied_lr=0.62,
        ep=0.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "ep_zero_and_lr_blank": dict(
        method="Reported BF", reported_cdf=1.6, paid_cdf=2.1, implied_lr=None,
        ep=0.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "elr_without_lr": dict(
        method="ELR", reported_cdf=1.6, paid_cdf=2.1, implied_lr=None,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "paid_bf_dead_paid_cdf": dict(
        method="Paid BF", reported_cdf=9.9, paid_cdf=1.0, implied_lr=0.62,
        ep=100_000.0, paid_claims=25_000.0, os_claims=5_000.0, reported_claims=40_000.0,
    ),
    "reported_cl_at_cdf_one": dict(
        method="Reported CL", reported_cdf=1.0, paid_cdf=1.0, implied_lr=None,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "paid_cl_healthy": dict(
        method="Paid CL", reported_cdf=1.0, paid_cdf=2.1, implied_lr=None,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
    ),
    "large_claim_add_back": dict(
        method="Reported CL", reported_cdf=1.4, paid_cdf=2.1, implied_lr=None,
        ep=100_000.0, paid_claims=25_000.0, os_claims=15_000.0, reported_claims=40_000.0,
        large_paid=2_000.0, large_incurred=5_000.0,
    ),
    "unknown_method": dict(
        method="Reported = Ultimate", reported_cdf=1.0, paid_cdf=1.0, implied_lr=None,
        ep=0.0, paid_claims=0.0, os_claims=0.0, reported_claims=0.0,
    ),
}


def build() -> dict:
    out = {}
    for name, case in CASES.items():
        method = case["method"]
        kwargs = {k: v for k, v in case.items() if k != "method"}
        out[name] = {
            "input": case,
            "ultimate": ultimate_for_method(method, **kwargs),
            "notes": notes_payload(method, **kwargs),
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {len(CASES)} cases -> {args.out}")


if __name__ == "__main__":
    main()
