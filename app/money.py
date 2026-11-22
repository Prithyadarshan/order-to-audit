"""Money, quantity and GST arithmetic.

Rules that keep the books exact:
  * money is an int number of paise (never float)
  * quantities are int base units: grams, millilitres, pieces
  * every division goes through round_div (half away from zero)
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def round_div(n: int, d: int) -> int:
    if d == 0:
        raise ZeroDivisionError("division by zero")
    sign = -1 if (n < 0) != (d < 0) else 1
    n, d = abs(n), abs(d)
    return sign * ((2 * n + d) // (2 * d))


def D(x) -> Decimal:
    try:
        return Decimal(str(x).strip())
    except InvalidOperation:
        raise ValueError(f"'{x}' is not a valid number") from None


def rupees_to_paise(x) -> int:
    return int((D(x) * 100).quantize(Decimal(1), ROUND_HALF_UP))


def paise_str(p: int) -> str:
    """7875 -> '78.75' (plain, for CSV/exports)."""
    sign = "-" if p < 0 else ""
    p = abs(int(p))
    return f"{sign}{p // 100}.{p % 100:02d}"


def inr(p: int) -> str:
    """Indian digit grouping: 1234567 paise -> '₹12,345.67'."""
    sign = "-" if p < 0 else ""
    p = abs(int(p))
    whole, frac = str(p // 100), p % 100
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}.{frac:02d}"


# ---------------------------------------------------------------- units
_MULT = {"kg": 1000, "g": 1, "l": 1000, "litre": 1000, "liter": 1000, "ml": 1,
         "pcs": 1, "piece": 1, "pieces": 1, "packet": 1, "packets": 1}


def allowed_units(item) -> list[str]:
    if item.base_unit == "g":
        return ["kg", "g"]
    if item.base_unit == "ml":
        return ["l", "ml"]
    if item.units_per_carton:
        return ["packet", "carton"]
    return ["pcs"]


def to_base(item, qty, unit: str | None = None) -> int:
    """Convert a user-entered quantity+unit to the item's integer base unit."""
    unit = (unit or item.display_unit).strip().lower()
    if unit == "carton":
        if not item.units_per_carton:
            raise ValueError(f"{item.name} has no carton size defined")
        mult = item.units_per_carton
    elif unit in _MULT:
        mult = _MULT[unit]
        if unit in ("kg", "g") and item.base_unit != "g" or unit in ("l", "litre", "liter", "ml") and item.base_unit != "ml":
            raise ValueError(f"Cannot use '{unit}' for {item.name} (stocked in {item.display_unit})")
    else:
        raise ValueError(f"Unknown unit '{unit}'")
    v = D(qty) * mult
    if v != v.to_integral_value():
        raise ValueError(f"{qty} {unit} is finer than the smallest unit tracked for {item.name}")
    return int(v)


def fmt_qty(item, base: int) -> str:
    q = Decimal(int(base)) / item.factor
    return format(q.quantize(Decimal("0.001")).normalize(), "f")


def value_of(item, qty_base: int, rate_paise_per_display_unit: int) -> int:
    return round_div(qty_base * rate_paise_per_display_unit, item.factor)


def pct_var(actual: int, planned: int) -> Decimal:
    if not planned:
        return Decimal(0)
    return ((Decimal(actual) - planned) * 100 / planned).quantize(Decimal("0.01"), ROUND_HALF_UP)


# ------------------------------------------------------------------ GST
def gst_split(taxable: int, gst_bp: int, intra_state: bool) -> tuple[int, int, int]:
    """Return (cgst, sgst, igst) in paise. gst_bp is the rate in basis points (5% = 500)."""
    tax = round_div(taxable * gst_bp, 10000)
    if intra_state:
        cgst = tax // 2
        return cgst, tax - cgst, 0
    return 0, 0, tax


def financial_year(d) -> str:
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen "
         "Sixteen Seventeen Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))
    elif n > 0:
        out.append(_ONES[n])
    return " ".join(out)


def amount_words(paise: int) -> str:
    rupees, ps = divmod(int(paise), 100)
    parts = []
    for div, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand")):
        if rupees >= div:
            parts.append(_below_1000(rupees // div) + " " + name)
            rupees %= div
    if rupees or not parts:
        parts.append(_below_1000(rupees) if rupees else "Zero")
    text = "Rupees " + " ".join(parts)
    if ps:
        text += " and " + _below_1000(ps) + " Paise"
    return text + " Only"
