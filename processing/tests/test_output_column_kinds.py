"""Unit tests for output column classification (ratio / factor / number)."""

from django.test import SimpleTestCase

from processing.output_column_kinds import (
    COUNT,
    FACTOR,
    NUMBER,
    RATIO,
    classify_columns,
    classify_rows,
)


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


# ---------------------------------------------------------------------------
# WP7 — row-kind classification for triangle sheets
# ---------------------------------------------------------------------------


class ClassifyRowsTests(SimpleTestCase):
    """A triangle sheet's kind varies DOWN the sheet, not across it.

    Cumulative money (3,463,357), an age-to-age factor (1.015748) and a factor count (3) sit in
    the same columns. Classified per column they are all `number`, and the preview's decimal
    heuristic then renders the factor as `1.01` — an actuary cannot read their own development
    factors. These tests pin the row-wise pass that fixes it.
    """

    PAID = [
        "2016-Q1", "2016-Q2", None,
        "Cumulative Triangle", "Accident Period", "2016-Q1", None,
        "Age-to-Age Factors", "Accident Period", "2016-Q1", "2016-Q2", None,
        "Accident Period",
        "Simple Avg LDF", "Simple Avg CDF", "Weighted Avg LDF", "Weighted Avg CDF",
        "Ex-Hi-Lo Avg LDF", "Last 4 Avg CDF", "Last 8 Avg LDF", "Median LDF", "Median CDF",
        "Factor Count", None, "Selected LDF", "Selected CDF",
    ]

    def kinds(self, labels=None, sheet="Paid Claims Triangle"):
        return dict(zip(labels or self.PAID, classify_rows(sheet, labels or self.PAID)))

    def test_only_triangle_sheets_are_row_classified(self):
        """None, not [] — the caller must be able to tell 'column-classified' from 'no rows'."""
        self.assertIsNone(classify_rows("Reserve Summary", ["a", "b"]))
        self.assertIsNone(classify_rows("Combined Summary", []))
        self.assertIsNotNone(classify_rows("Paid Claims Triangle", []))
        self.assertIsNotNone(classify_rows("Reported Triangle", []))

    def test_rows_inherit_the_block_label_above_them(self):
        k = self.kinds()
        self.assertEqual(k["2016-Q2"], FACTOR)   # last occurrence: inside the a2a block
        self.assertEqual(k["Simple Avg LDF"], FACTOR)

    def test_the_leading_block_is_money_on_both_sheets(self):
        """It has no label row above it, so its kind comes from the sheet name."""
        self.assertEqual(classify_rows("Paid Claims Triangle", ["2016-Q1"]), [NUMBER])
        self.assertEqual(classify_rows("Reported Triangle", ["2016-Q1"]), [NUMBER])

    def test_every_benchmark_row_is_a_factor(self):
        k = self.kinds()
        for label in (
            "Simple Avg LDF", "Simple Avg CDF", "Weighted Avg LDF", "Weighted Avg CDF",
            "Ex-Hi-Lo Avg LDF", "Last 4 Avg CDF", "Last 8 Avg LDF", "Median LDF",
            "Median CDF", "Selected LDF", "Selected CDF",
        ):
            self.assertEqual(k[label], FACTOR, label)

    def test_a_new_average_basis_is_a_factor_without_being_listed(self):
        """Matched by the LDF/CDF suffix, not an allowlist: WP1 grew this block from four rows
        to thirteen, and the next basis must not silently render as money."""
        labels = ["Age-to-Age Factors", "Geometric Mean LDF", "Geometric Mean CDF"]
        self.assertEqual(
            classify_rows("Paid Claims Triangle", labels)[1:], [FACTOR, FACTOR]
        )

    def test_factor_count_is_a_tally_not_a_measurement(self):
        self.assertEqual(self.kinds()["Factor Count"], COUNT)

    def test_a_repeated_header_row_holds_development_numbers_not_factors(self):
        """`Accident Period` rows carry 0, 1, 2 ... — integers. Inheriting the age-to-age
        block's kind would render the column headings as 0.0000, 1.0000, ..."""
        self.assertEqual(self.kinds()["Accident Period"], COUNT)

    def test_an_unknown_label_falls_back_to_its_block(self):
        labels = ["Age-to-Age Factors", "Something Unexpected"]
        self.assertEqual(classify_rows("Paid Claims Triangle", labels)[1], FACTOR)
        labels = ["Cumulative Triangle", "Something Unexpected"]
        self.assertEqual(classify_rows("Paid Claims Triangle", labels)[1], NUMBER)

    def test_reserve_summary_column_classification_is_untouched(self):
        headers = [
            "Accident_Period", "EP", "Paid Claims", "Reported LR", "Large Paid",
            "Implied LR", "Paid CDF", "IBNR", "ULR", "CDF",
        ]
        self.assertEqual(
            classify_columns("Reserve Summary", headers),
            [NUMBER, NUMBER, NUMBER, RATIO, NUMBER, RATIO, FACTOR, NUMBER, RATIO, FACTOR],
        )
