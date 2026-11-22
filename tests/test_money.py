from datetime import date
from decimal import Decimal

import pytest

from app.money import (amount_words, financial_year, fmt_qty, gst_split, inr, pct_var, round_div,
                       rupees_to_paise, to_base)


class FakeItem:
    def __init__(self, base, disp, factor, per_carton=None, name="x"):
        self.base_unit, self.display_unit, self.factor, self.units_per_carton, self.name = base, disp, factor, per_carton, name


def test_round_div_half_away_from_zero():
    assert round_div(5, 2) == 3 and round_div(-5, 2) == -3 and round_div(4, 2) == 2 and round_div(1, 3) == 0


def test_rupees_to_paise_no_float_drift():
    assert rupees_to_paise("0.1") + rupees_to_paise("0.2") == 30
    assert rupees_to_paise("2.50") == 250 and rupees_to_paise("43.125") == 4313


def test_indian_grouping():
    assert inr(787500) == "₹7,875.00" and inr(123456789) == "₹12,34,567.89" and inr(-5) == "-₹0.05"


def test_units_convert():
    flour, oil = FakeItem("g", "kg", 1000), FakeItem("ml", "L", 1000)
    fg = FakeItem("pcs", "packet", 1, per_carton=20)
    assert to_base(flour, "40") == 40000 and to_base(flour, "250", "g") == 250
    assert to_base(oil, "10.5") == 10500 and to_base(oil, "500", "ml") == 500
    assert to_base(fg, "3", "carton") == 60 and to_base(fg, "5", "packet") == 5
    assert fmt_qty(oil, 19500) == "19.5" and fmt_qty(flour, 120000) == "120"
    with pytest.raises(ValueError):
        to_base(flour, "0.0005")        # finer than a gram
    with pytest.raises(ValueError):
        to_base(oil, "1", "kg")         # wrong unit family


def test_gst_split():
    assert gst_split(750000, 500, True) == (18750, 18750, 0)
    assert gst_split(600000, 500, False) == (0, 0, 30000)
    c, s, _ = gst_split(101, 500, True)  # 5.05 paise -> 5 ; odd paise never lost
    assert c + s == 5


def test_variance_and_fy():
    assert pct_var(188, 200) == Decimal("-6.00") and pct_var(10500, 10000) == Decimal("5.00")
    assert financial_year(date(2026, 3, 31)) == "2025-26" and financial_year(date(2026, 4, 1)) == "2026-27"


def test_words():
    assert amount_words(787500) == "Rupees Seven Thousand Eight Hundred Seventy Five Only"
    assert amount_words(12345050) == "Rupees One Lakh Twenty Three Thousand Four Hundred Fifty and Fifty Paise Only"
