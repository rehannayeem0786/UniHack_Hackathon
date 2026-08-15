"""Normalisation rules from the house style guide.

The inch-snapping tests exist because of a measured regression. Feeding retrieved
manufacturer specifications into the pipeline introduced decimal inches such as
`5.6 in` into descriptions, and rule compliance for "fractions not decimals"
fell from 97% to 86%. `snap_inches` is the fix, and these tests keep it fixed.
"""

from __future__ import annotations

import pytest

from backend.core.normalize import (
    abbreviate_for_invoice,
    canonical_uom,
    clean,
    decimal_to_fraction,
    is_placeholder,
    normalize_measure_text,
    repair_symbols,
    snap_decimal_inches,
    snap_inches,
    space_units,
    title_case,
    truncate_clean,
)


class TestPlaceholders:
    @pytest.mark.parametrize(
        "value",
        ["-- Unbranded --", "-- No Unilog Brand --", "-- No DIB Brand --", "-", "", "   "],
    )
    def test_placeholders_are_not_data(self, value):
        assert is_placeholder(value) is True

    def test_real_values_survive(self):
        assert is_placeholder("TREX") is False
        assert is_placeholder("Satco") is False


class TestDecimalToFraction:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.5, "1/2"),
            (50.25, "50-1/4"),
            (0.0625, "1/16"),
            (8.5, "8-1/2"),
            (3.0, "3"),
        ],
    )
    def test_exact_trade_fractions(self, value, expected):
        assert decimal_to_fraction(value) == expected

    def test_refuses_to_invent_precision(self):
        # 1.68 is not a trade fraction; turning it into 1-17/25 would be worse
        # than leaving it alone, which is the documented intent.
        assert decimal_to_fraction(1.68) == "1.68"
        assert decimal_to_fraction(5.6) == "5.6"

    def test_non_numeric_passes_through(self):
        assert decimal_to_fraction("Stainless Steel") == "Stainless Steel"


class TestSnapInches:
    def test_exact_fractions_are_unchanged(self):
        assert snap_inches(8.5) == "8-1/2"
        assert snap_inches(50.25) == "50-1/4"

    @pytest.mark.parametrize(
        "value,expected",
        [
            (5.6, "5-5/8"),      # 5.625, off by 0.025 in
            (6.13, "6-1/8"),     # 6.125
            (11.06, "11-1/16"),  # 11.0625
            (8.9, "8-7/8"),      # 8.875
        ],
    )
    def test_awkward_decimals_snap_to_a_sixteenth(self, value, expected):
        assert snap_inches(value) == expected

    def test_rounding_up_into_the_next_whole_inch(self):
        assert snap_inches(7.98) == "8"

    def test_whole_numbers_stay_whole(self):
        assert snap_inches(12.0) == "12"

    def test_non_numeric_passes_through(self):
        assert snap_inches("Keyless") == "Keyless"


class TestSnapDecimalInches:
    def test_rewrites_only_inch_measurements(self):
        assert snap_decimal_inches("5.6 in tall") == "5-5/8 in tall"
        assert snap_decimal_inches('3.25"') == "3-1/4 in"
        assert snap_decimal_inches("8.5in") == "8-1/2 in"

    def test_leaves_other_units_alone(self):
        # Only inches are governed by the fraction rule; a decimal weight or
        # voltage is written as published.
        assert snap_decimal_inches("3.23 lb") == "3.23 lb"
        assert snap_decimal_inches("14.4 V") == "14.4 V"

    def test_the_regression_case_end_to_end(self):
        # This exact shape came out of a retrieved DeWALT specification and put a
        # decimal inch into LONG_DESC.
        text = "Assembled height 5.6 in, width 6.13 in, length 11.06 in"
        cleaned = normalize_measure_text(text)
        assert "5-5/8 in" in cleaned
        assert "6-1/8 in" in cleaned
        assert "11-1/16 in" in cleaned
        assert ".6 in" not in cleaned


class TestUom:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("inches", "in"), ("IN.", "in"), ('"', "in"), ("inch", "in"),
            ("pounds", "lb"), ("lbs", "lb"), ("volts", "V"), ("amps", "A"),
            ("dba", "dBA"), ("rpm", "RPM"),
        ],
    )
    def test_canonical_forms(self, raw, expected):
        assert canonical_uom(raw) == expected

    def test_unknown_unit_is_preserved_not_dropped(self):
        assert canonical_uom("furlongs") != ""


class TestSpacing:
    def test_number_and_unit_are_separated(self):
        assert space_units("24in") == "24 in"
        assert "15 A" in space_units("15A")

    def test_measure_text_handles_inch_marks(self):
        assert normalize_measure_text('50-1/4"') == "50-1/4 in"


class TestSymbols:
    def test_mojibake_is_repaired(self):
        # Written as escapes rather than literals so the test cannot itself be
        # corrupted by the encoding problem it is checking for.
        assert repair_symbols("FRIGIDAIRE\u00c2\u00ae") == "FRIGIDAIRE\u00ae"
        assert repair_symbols("CleanBoost\u00e2\u201e\u00a2") == "CleanBoost\u2122"

    def test_clean_collapses_whitespace(self):
        assert clean("  a   b \n c ") == "a b c"


class TestCasing:
    def test_small_words_stay_lowercase_inside_a_title(self):
        assert title_case("dishwasher with legs") == "Dishwasher with Legs"

    def test_technical_tokens_stay_upper(self):
        assert "LED" in title_case("led downlight")
        assert "GFCI" in title_case("gfci receptacle")


class TestInvoice:
    def test_abbreviates_and_truncates(self):
        out = abbreviate_for_invoice("DISHWASHER STAINLESS STEEL ASSEMBLY", 40)
        assert len(out) <= 40
        assert out == out.upper()

    def test_truncate_clean_does_not_cut_mid_word(self):
        assert truncate_clean("stainless steel dishwasher", 12).endswith(("steel", "stainless"))
