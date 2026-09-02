"""WP0 — reserving-class reconciliation.

The defect this guards: the engine joins premium to claims by exact string equality on
RESERVINGCLASS and nothing raises on a mismatch. On the reference book the claims files say
`Health` where premium says `Health Insurance`, so 3,044 paid rows worth 35,503,674 — the
largest class of the book — are discarded, the run succeeds, and the workbook looks fine.
"""

import pandas as pd
from django.test import SimpleTestCase

from core.normalize import canonical_key, core_tokens, match_score, suggest_matches
from processing.services.preflight import (
    SEVERITY_ERROR,
    SEVERITY_OK,
    SEVERITY_WARN,
    build_preflight_report,
)


def _premium(*classes, rows=1):
    return pd.DataFrame({"RESERVINGCLASS": [c for c in classes for _ in range(rows)]})


def _claims(pairs, *, os=False):
    """pairs: (class, amount). Builds the columns `import_data` would produce."""
    column = "AMOUNTOUTSTANDING" if os else "AMOUNTPAID"
    return pd.DataFrame(
        {
            "RESERVINGCLASS": [c for c, _ in pairs],
            column: [a for _, a in pairs],
            "POLICYCLASS": ["Fire"] * len(pairs),
            "HEADOFDAMAGE": ["Payment"] * len(pairs),
            "AMOUNTRECOVERED": [0] * len(pairs),
        }
    )


class CanonicalKeyTests(SimpleTestCase):
    def test_case_whitespace_and_punctuation_never_need_a_human(self):
        for a, b in (
            ("Health Insurance", "health insurance"),
            ("  Health   Insurance  ", "Health Insurance"),
            ("Health Insurance.", "Health Insurance"),
            ("Banker's Blanket", "Bankers Blanket"),
        ):
            self.assertEqual(canonical_key(a), canonical_key(b), f"{a!r} vs {b!r}")

    def test_genuinely_different_names_keep_different_keys(self):
        self.assertNotEqual(canonical_key("Health"), canonical_key("Health Insurance"))
        self.assertNotEqual(canonical_key("Motor"), canonical_key("Marine"))

    def test_a_name_made_only_of_qualifiers_does_not_collapse_to_nothing(self):
        """Otherwise `Insurance` would have empty core tokens and match every class."""
        self.assertEqual(core_tokens("Insurance"), ("insurance",))


class MatchScoreTests(SimpleTestCase):
    def test_the_reference_pairing_scores_at_the_top(self):
        """difflib's plain ratio gives this 0.545 and would miss it at any usable threshold —
        which is why the scorer leads with the qualifier shape instead."""
        score, basis = match_score("Health", "Health Insurance")
        self.assertEqual(score, 1.0)
        self.assertIn("qualifier", basis)

    def test_distinct_classes_are_not_suggested(self):
        for a, b in (("Motor Insurance", "Marine Insurance"), ("D&O", "Health Insurance")):
            score, _ = match_score(a, b)
            self.assertLess(score, 0.8, f"{a!r} vs {b!r} scored {score}")

    def test_suggestions_are_ranked_and_capped(self):
        out = suggest_matches("Health", ["Health Insurance", "Motor Insurance", "health"])
        self.assertEqual(out[0].candidate, "Health Insurance")
        self.assertTrue(all(s.score >= 0.8 for s in out))
        self.assertLessEqual(len(suggest_matches("Health", ["Health"] * 50)), 10)


class ReconciliationTests(SimpleTestCase):
    def test_a_consistent_book_passes(self):
        report = build_preflight_report(
            _premium("Motor", "Fire"),
            _claims([("Motor", 10), ("Fire", 20)]),
            _claims([("Motor", 5), ("Fire", 7)], os=True),
        )
        self.assertEqual(report.severity, SEVERITY_OK)
        self.assertEqual(report.dropped_row_count, 0)
        self.assertFalse(report.blocking)

    def test_a_claims_class_absent_from_premium_is_an_error_with_its_value(self):
        report = build_preflight_report(
            _premium("Health Insurance"),
            _claims([("Health", 100), ("Health", 250)]),
            _claims([("Health Insurance", 5)], os=True),
        )
        self.assertEqual(report.severity, SEVERITY_ERROR)
        self.assertEqual(report.dropped_row_count, 2)
        self.assertEqual(report.dropped_amount, 350.0)
        codes = {m.code for m in report.messages}
        self.assertIn("class_not_in_premium", codes)
        self.assertIn("rows_discarded", codes)

    def test_it_proposes_the_alias_that_would_fix_it(self):
        report = build_preflight_report(
            _premium("Health Insurance"),
            _claims([("Health", 100)]),
            _claims([("Health Insurance", 5)], os=True),
        )
        self.assertEqual(
            report.suggestions[0]["alias"], "Health"
        )
        self.assertEqual(report.suggestions[0]["canonical"], "Health Insurance")

    def test_a_premium_class_with_os_but_no_paid_is_an_error(self):
        """The other half of the same defect: the class still produces a workbook, and its
        reserve develops from outstanding alone."""
        report = build_preflight_report(
            _premium("Health Insurance"),
            _claims([("Motor", 1)]),
            _claims([("Health Insurance", 5)], os=True),
        )
        codes = {m.code for m in report.messages}
        self.assertIn("class_without_paid_claims", codes)
        self.assertEqual(report.severity, SEVERITY_ERROR)

    def test_a_class_with_no_claims_at_all_only_warns(self):
        """The D&O case. Making this an error would train users into permissive mode and the
        gate would stop meaning anything."""
        report = build_preflight_report(
            _premium("Motor", "D&O"),
            _claims([("Motor", 1)]),
            _claims([("Motor", 1)], os=True),
        )
        self.assertEqual(report.severity, SEVERITY_WARN)
        self.assertFalse(report.blocking)
        self.assertIn("class_without_claims", {m.code for m in report.messages})

    def test_case_differences_alone_reconcile_without_an_alias(self):
        report = build_preflight_report(
            _premium("Motor Insurance"),
            _claims([("MOTOR INSURANCE", 1)]),
            _claims([("motor insurance", 1)], os=True),
        )
        self.assertEqual(report.severity, SEVERITY_OK)

    def test_dropped_amount_uses_the_engine_recovery_substitution(self):
        """`import_data` swaps AMOUNTRECOVERED in for AMOUNTPAID on Motor recovery heads, so a
        raw AMOUNTPAID sum would misstate what is being discarded."""
        frame = pd.DataFrame(
            {
                "RESERVINGCLASS": ["Ghost", "Ghost"],
                "AMOUNTPAID": [100.0, 100.0],
                "AMOUNTRECOVERED": [7.0, 0.0],
                "POLICYCLASS": ["Motor", "Fire"],
                "HEADOFDAMAGE": ["Salvage", "Payment"],
            }
        )
        report = build_preflight_report(_premium("Motor"), frame, _claims([("Motor", 1)], os=True))
        self.assertEqual(report.dropped_amount, 107.0)

    def test_an_empty_claims_file_is_an_error_not_a_silent_book_of_zeros(self):
        report = build_preflight_report(_premium("Motor"), None, _claims([("Motor", 1)], os=True))
        self.assertEqual(report.severity, SEVERITY_ERROR)
        self.assertIn("empty_input", {m.code for m in report.messages})

    def test_row_counts_line_the_three_inputs_up_side_by_side(self):
        report = build_preflight_report(
            _premium("Motor", rows=3),
            _claims([("Motor", 1), ("Motor", 2)]),
            _claims([("Motor", 1)], os=True),
        )
        self.assertEqual(report.row_counts["Motor"], {"premium": 3, "paid": 2, "os": 1})

    def test_the_report_round_trips_to_json_safe_primitives(self):
        import json

        report = build_preflight_report(
            _premium("Health Insurance"), _claims([("Health", 1)]), _claims([("Health Insurance", 1)], os=True)
        )
        json.dumps(report.as_dict())  # must not raise
