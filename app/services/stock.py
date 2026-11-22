"""Ledger-based stock. Balance = SUM(ledger). Issues are valued at weighted-average cost."""
from datetime import date

from sqlalchemy import func, select

from ..errors import StockError
from ..models import ProductionRun, StockLedger
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


def batch_balances(db, item_id: int) -> list[tuple[str | None, int]]:
    """(batch_no, qty) with stock on hand, oldest expiry first. Stock without a batch (opening balance)
    counts as oldest. This is what first-expiry-first-out dispatch consumes."""
    rows = db.execute(select(StockLedger.batch_no, func.sum(StockLedger.qty_base)).where(StockLedger.item_id == item_id)
                      .group_by(StockLedger.batch_no)).all()
    live = [(b, int(q)) for b, q in rows if q and int(q) > 0]
    exp = {b: e for b, e in db.execute(select(ProductionRun.batch_no, ProductionRun.expiry)
                                       .where(ProductionRun.batch_no.in_([b for b, _ in live if b])))}
    return sorted(live, key=lambda x: (x[0] is not None, exp.get(x[0]) or date.min, x[0] or ""))


def issue_fefo(db, item, qty: int, doc_type: str, **kw) -> int:
    """Like issue(), but takes stock from the batches that expire first. One ledger row per batch used,
    the total value is the weighted-average value split in proportion to quantity."""
    if qty <= 0:
        raise StockError(f"Quantity for {item.name} must be positive")
    have, _ = balance(db, item.id)
    if qty > have:
        raise StockError(f"Insufficient stock of {item.name}: have {fmt_qty(item, have)} {item.display_unit}, "
                         f"need {fmt_qty(item, qty)} {item.display_unit}")
    total = wac_value(db, item.id, qty)
    remaining, spent, parts = qty, 0, []
    for batch, bq in batch_balances(db, item.id):
        if remaining == 0:
            break
        take = min(bq, remaining)
        parts.append((batch, take))
        remaining -= take
    for n, (batch, take) in enumerate(parts):
        value = total - spent if n == len(parts) - 1 else round_div(total * take, qty)
        spent += value
        post(db, item, -take, -value, doc_type, batch_no=batch, **kw)
    return total
