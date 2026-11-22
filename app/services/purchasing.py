from datetime import date

from sqlalchemy import func, select

from ..errors import BusinessError
from ..models import Purchase, PurchaseLine
from ..money import value_of
from . import audit, stock


def record_purchase(db, user, supplier_id: int, bill_no: str | None, lines, on: date | None = None) -> Purchase:
    """lines: iterable of (item, qty_base, rate_paise_per_display_unit). Receipts are valued at purchase cost."""
    lines = [l for l in lines if l[1] > 0]
    if not lines:
        raise BusinessError("Add at least one item with a quantity")
    on = on or date.today()
    n = db.scalar(select(func.count(Purchase.id))) + 1
    p = Purchase(ref=f"PUR-{n:04d}", supplier_id=supplier_id, bill_no=bill_no, date=on, created_by=user.id)
    db.add(p)
    db.flush()
    for item, qty, rate in lines:
        if item.kind == "FG":
            raise BusinessError("Finished goods are produced, not purchased")
        amount = value_of(item, qty, rate)
        p.lines.append(PurchaseLine(item_id=item.id, qty_base=qty, rate_paise=rate, amount_paise=amount))
        stock.receive(db, item, qty, amount, "PURCHASE", doc_id=p.id, doc_ref=p.ref, user=user, entry_date=on)
    audit.log(db, user, "purchase", p.id, "create", f"{p.ref} total {p.total_paise}")
    return p
