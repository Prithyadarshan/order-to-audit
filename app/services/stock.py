"""Ledger-based stock. Balance = SUM(ledger). Issues are valued at weighted-average cost."""
from datetime import date

from sqlalchemy import func, select

from ..errors import StockError
from ..models import StockLedger
from ..money import fmt_qty, round_div


def post(db, item, qty, value, doc_type, doc_id=None, doc_ref=None, user=None, note=None, batch_no=None, entry_date=None):
    row = StockLedger(item_id=item.id, qty_base=qty, value_paise=value, doc_type=doc_type, doc_id=doc_id,
                      doc_ref=doc_ref, user_id=getattr(user, "id", None), note=note, batch_no=batch_no,
                      entry_date=entry_date or date.today())
    db.add(row)
    db.flush()
    return row


def balance(db, item_id: int) -> tuple[int, int]:
    q, v = db.execute(select(func.coalesce(func.sum(StockLedger.qty_base), 0),
                             func.coalesce(func.sum(StockLedger.value_paise), 0))
                      .where(StockLedger.item_id == item_id)).one()
    return int(q), int(v)


def balances(db) -> dict[int, tuple[int, int]]:
    rows = db.execute(select(StockLedger.item_id, func.sum(StockLedger.qty_base), func.sum(StockLedger.value_paise))
                      .group_by(StockLedger.item_id)).all()
    return {i: (int(q), int(v)) for i, q, v in rows}


def wac_value(db, item_id: int, qty: int) -> int:
    """Paise value of `qty` base units at the current weighted-average cost."""
    bq, bv = balance(db, item_id)
    if bq <= 0:
        return 0
    return bv if qty >= bq else round_div(bv * qty, bq)


def receive(db, item, qty, value, doc_type, **kw):
    return post(db, item, qty, value, doc_type, **kw)


def issue(db, item, qty, doc_type, **kw) -> int:
    """Take `qty` out of stock; refuses to go negative. Returns the paise value removed."""
    if qty <= 0:
        raise StockError(f"Quantity for {item.name} must be positive")
    have, _ = balance(db, item.id)
    if qty > have:
        raise StockError(f"Insufficient stock of {item.name}: have {fmt_qty(item, have)} {item.display_unit}, "
                         f"need {fmt_qty(item, qty)} {item.display_unit}")
    value = wac_value(db, item.id, qty)
    post(db, item, -qty, -value, doc_type, **kw)
    return value


def adjust(db, user, item, qty: int, reason: str, entry_date=None):
    """Manual correction (+/-). Positive adjustments are valued at the current average cost."""
    if qty == 0 or not reason.strip():
        raise StockError("An adjustment needs a non-zero quantity and a reason")
    if qty < 0:
        return issue(db, item, -qty, "ADJUST", user=user, note=reason, entry_date=entry_date)
    bq, bv = balance(db, item.id)
    value = round_div(bv * qty, bq) if bq > 0 else 0
    post(db, item, qty, value, "ADJUST", user=user, note=reason, entry_date=entry_date)
    return value
