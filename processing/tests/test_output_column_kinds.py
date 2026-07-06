"""Unit tests for output column classification (ratio / factor / number)."""

from django.test import SimpleTestCase

from processing.output_column_kinds import FACTOR, NUMBER, RATIO, classify_columns


class ClassifyColumnsTests(SimpleTestCase):
    def test_named_ratio_columns(self):
        headers = [
            "Paid LR",
            "Inc LR",
            "Ult LR",
            "Reported LR",
            "Implied LR",
            "ULR",
            "Selected ULR",
            "Comm Ratio",
            "Combined Ratio",
            "Exp Ratio",
            "RI %",
            "RA %",
            "ULAE %",
            "EP_Percent",
            "Cumulative %",
            "Expected Unpaid %",
        ]
        kinds = classify_columns("UW Summary", headers)
        self.assertTrue(all(k == RATIO for k in kinds), kinds)

    def test_development_and_discount_factors(self):
        headers = [
            "CDF",
            "Paid CDF",
            "Reported CDF",
            "Selected CDF",
            "Selected LDF",
            "Simple Avg CDF",
            "Weighted Avg LDF",
            "CY Discount Factor",
            "PY Discount Factor",
        ]
        kinds = classify_columns("Reserve Summary", headers)
        self.assertTrue(all(k == FACTOR for k in kinds), kinds)

    def test_money_and_id_columns_default_to_number(self):
        # Note the traps: LRC / LC names contain ratio-ish substrings but are money.
        headers = [
            "RESERVINGCLASS",
            "UWY",
            "GWP",
            "Gross UPR",
            "Gross LRC",
            "PAA_LRC",
            "GMM LRC_Undiscounted",
            "LC Discounted_CY",
            "Loss Recovery Component",
            "Ultimate Claims",
            "IBNR",
        ]
        kinds = classify_columns("Combined Summary", headers)
        self.assertEqual(kinds, [NUMBER] * len(headers))

    def test_matching_is_case_and_whitespace_insensitive(self):
        self.assertEqual(classify_columns("x", ["  paid   lr "]), [RATIO])
        self.assertEqual(classify_columns("x", ["ri %"]), [RATIO])
        self.assertEqual(classify_columns("x", ["PAID CDF"]), [FACTOR])

    def test_discount_rate_vs_discount_factor_are_not_confused(self):
        # "... Discount Factor" is a factor; a bare/unknown "Discount" name is not
        # forced into a percentage. Exact matching prevents substring collisions.
        self.assertEqual(classify_columns("x", ["CY Discount Factor"]), [FACTOR])
        self.assertEqual(classify_columns("x", ["Quarterly Discount Rate"]), [NUMBER])

    def test_payment_pattern_numeric_columns_are_ratios(self):
        headers = ["RESERVINGCLASS", 0, 1, 2, 3]
        kinds = classify_columns("Payment Pattern", headers)
        self.assertEqual(kinds, [NUMBER, RATIO, RATIO, RATIO, RATIO])

    def test_numeric_columns_are_money_on_non_ratio_sheets(self):
        # Same integer-named columns are money on FutureCF, not ratios.
        headers = ["RESERVINGCLASS", 0, 1, 2]
        kinds = classify_columns("FutureCF", headers)
        self.assertEqual(kinds, [NUMBER, NUMBER, NUMBER, NUMBER])

    def test_length_and_order_preserved(self):
        headers = ["UWY", "Paid LR", "GWP", "Paid CDF"]
        kinds = classify_columns("UW Summary", headers)
        self.assertEqual(kinds, [NUMBER, RATIO, NUMBER, FACTOR])
