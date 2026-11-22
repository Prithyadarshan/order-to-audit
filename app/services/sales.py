"""Order -> approval -> GST invoice -> dispatch -> payment, plus cancellation via credit note."""
from datetime import date

from sqlalchemy import func, select

from .. import config
from ..errors import BusinessError
from ..models import Customer, CreditNote, Invoice, InvoiceLine, Item, Order, OrderLine, Payment, now
from ..money import financial_year, gst_split
from . import audit, stock

OPEN_STATES = ("SUBMITTED", "APPROVED", "INVOICED")


# ------------------------------------------------------------------ orders
def create_order(db, user, customer_id: int, lines, note: str | None = None, client_ref: str | None = None) -> Order:
    """lines: iterable of (item, qty_packets). Idempotent on client_ref so offline re-sync never duplicates."""
    if client_ref:
        existing = db.scalar(select(Order).where(Order.client_ref == client_ref))
        if existing:
            return existing
    customer = db.get(Customer, customer_id)
    if not customer or not customer.active:
        raise BusinessError("Pick a valid customer")
    merged: dict[int, int] = {}
    for item, qty in lines:
        if qty > 0:
            merged[item.id] = merged.get(item.id, 0) + qty
    if not merged:
        raise BusinessError("Add at least one product with a quantity")
    n = db.scalar(select(func.count(Order.id))) + 1
    order = Order(order_no=f"ORD-{n:04d}", client_ref=client_ref, customer_id=customer_id, salesman_id=user.id, note=note)
    db.add(order)
    for item_id, qty in merged.items():
        item = db.get(Item, item_id)
        if item.kind != "FG":
            raise BusinessError(f"{item.name} is not a sellable product")
        order.lines.append(OrderLine(item_id=item_id, qty=qty, price_paise=item.price_paise))
    db.flush()
    audit.log(db, user, "order", order.id, "create", f"{order.order_no} {customer.name}")
    return order


def reserved_qty(db, item_id: int, exclude_order_id: int | None = None) -> int:
    q = (select(func.coalesce(func.sum(OrderLine.qty), 0)).join(Order)
         .where(OrderLine.item_id == item_id, Order.status.in_(("APPROVED", "INVOICED"))))
    if exclude_order_id:
        q = q.where(Order.id != exclude_order_id)
    return int(db.scalar(q))


def stock_check(db, order: Order) -> list[dict]:
    """Per line: required vs available (balance minus quantities promised to other approved orders)."""
    out = []
    for l in order.lines:
        have, _ = stock.balance(db, l.item_id)
        avail = max(have - reserved_qty(db, l.item_id, order.id), 0)
        out.append({"line": l, "item": l.item, "required": l.qty, "available": avail, "shortage": max(l.qty - avail, 0)})
    return out


def edit_lines(db, user, order: Order, changes: dict[int, tuple[int, int]]):
    """changes: line_id -> (qty, price_paise). qty 0 removes the line."""
    if order.status not in ("SUBMITTED", "APPROVED"):
        raise BusinessError("Only orders that are not yet invoiced can be edited")
    before = {l.id: (l.qty, l.price_paise) for l in order.lines}
    for l in list(order.lines):
        if l.id in changes:
            qty, price = changes[l.id]
            if qty <= 0:
                order.lines.remove(l)
            else:
                l.qty, l.price_paise = qty, price
    if not order.lines:
        raise BusinessError("An order needs at least one line - reject it instead")
    if order.status == "APPROVED":
        order.status = "SUBMITTED"  # any change needs a fresh approval
    audit.log(db, user, "order", order.id, "edit", f"before={before} after={ {l.id: (l.qty, l.price_paise) for l in order.lines} }")


def reduce_to_available(db, user, order: Order):
    changes = {c["line"].id: (min(c["required"], c["available"]), c["line"].price_paise) for c in stock_check(db, order)}
    if all(q == 0 for q, _ in changes.values()):
        raise BusinessError("Nothing is available in stock for this order")
    edit_lines(db, user, order, changes)


def approve(db, user, order: Order):
    if order.status != "SUBMITTED":
        raise BusinessError(f"Order is {order.status.lower()}, only submitted orders can be approved")
    short = [c for c in stock_check(db, order) if c["shortage"]]
    if short:
        raise BusinessError("Insufficient finished-goods stock: " + "; ".join(
            f"{c['item'].name} short by {c['shortage']}" for c in short) + ". Reduce the quantity, wait for production, or reject.")
    order.status = "APPROVED"
    audit.log(db, user, "order", order.id, "approve")


def reject(db, user, order: Order, reason: str):
    if order.status not in ("SUBMITTED", "APPROVED"):
        raise BusinessError("This order can no longer be rejected")
    order.status, order.reject_reason = "REJECTED", reason or "No reason given"
    audit.log(db, user, "order", order.id, "reject", order.reject_reason)


# --------------------------------------------------------------------- GST
def compute_tax(order_lines, customer_state: str):
    """order_lines: (item, qty, price). Returns (lines, totals) - used for invoices and the pre-approval preview."""
    intra = customer_state.strip().lower() == config.FACTORY["state"].lower()
    lines, tot = [], {"taxable": 0, "cgst": 0, "sgst": 0, "igst": 0}
    for item, qty, price in order_lines:
        taxable = qty * price
        c, s, i = gst_split(taxable, item.gst_bp, intra)
        lines.append({"item": item, "qty": qty, "rate": price, "taxable": taxable, "gst_bp": item.gst_bp, "cgst": c, "sgst": s, "igst": i})
        tot["taxable"] += taxable; tot["cgst"] += c; tot["sgst"] += s; tot["igst"] += i
    tot["total"] = tot["taxable"] + tot["cgst"] + tot["sgst"] + tot["igst"]
    return lines, tot, intra


def tax_preview(order: Order):
    return compute_tax([(l.item, l.qty, l.price_paise) for l in order.lines], order.customer.state)


# ----------------------------------------------------------------- invoice
def create_invoice(db, user, order: Order, on: date | None = None) -> Invoice:
    if order.status != "APPROVED":
        raise BusinessError("Only approved orders can be invoiced")
    on = on or date.today()
    fy = financial_year(on)
    seq = (db.scalar(select(func.max(Invoice.seq)).where(Invoice.fy == fy)) or 0) + 1
    lines, tot, intra = tax_preview(order)
    inv = Invoice(inv_no=f"INV/{fy}/{seq:04d}", fy=fy, seq=seq, order_id=order.id, customer_id=order.customer_id, date=on,
                  place_of_supply=order.customer.state, intra_state=intra, taxable_paise=tot["taxable"],
                  cgst_paise=tot["cgst"], sgst_paise=tot["sgst"], igst_paise=tot["igst"], total_paise=tot["total"],
                  created_by=user.id)
    db.add(inv)
    for l in lines:
        inv.lines.append(InvoiceLine(item_id=l["item"].id, description=l["item"].name, hsn=l["item"].hsn, qty=l["qty"],
                                     rate_paise=l["rate"], taxable_paise=l["taxable"], gst_bp=l["gst_bp"],
                                     cgst_paise=l["cgst"], sgst_paise=l["sgst"], igst_paise=l["igst"]))
    order.status = "INVOICED"
    db.flush()
    audit.log(db, user, "invoice", inv.id, "create", f"{inv.inv_no} total {inv.total_paise}")
    return inv


def dispatch(db, user, inv: Invoice):
    if inv.status != "ISSUED":
        raise BusinessError("Cancelled invoices cannot be dispatched")
    if inv.dispatched_at:
        raise BusinessError("Already dispatched")
    for l in inv.lines:  # StockError propagates if short: nothing is posted (transaction rolls back)
        stock.issue(db, l.item, l.qty, "DISPATCH", doc_id=inv.id, doc_ref=inv.inv_no, user=user)
    inv.dispatched_at = now()
    inv.order.status = "DISPATCHED"
    audit.log(db, user, "invoice", inv.id, "dispatch", inv.inv_no)


def paid(inv: Invoice) -> int:
    return sum(p.amount_paise for p in inv.payments)


def outstanding(inv: Invoice) -> int:
    return 0 if inv.status == "CANCELLED" else inv.total_paise - paid(inv)


def record_payment(db, user, inv: Invoice, amount: int, mode: str, ref: str | None, on: date | None = None) -> Payment:
    if inv.status != "ISSUED":
        raise BusinessError("Cannot record a payment against a cancelled invoice")
    if amount <= 0:
        raise BusinessError("Payment must be above zero")
    if amount > outstanding(inv):
        raise BusinessError("Payment is more than the amount outstanding on this invoice")
    p = Payment(customer_id=inv.customer_id, invoice_id=inv.id, date=on or date.today(), amount_paise=amount, mode=mode, ref=ref, created_by=user.id)
    db.add(p)
    inv.payments.append(p)
    db.flush()
    audit.log(db, user, "payment", p.id, "create", f"{inv.inv_no} {amount}")
    return p


def cancel_invoice(db, user, inv: Invoice, reason: str) -> CreditNote:
    """Posted invoices are never deleted: cancel with a credit note; dispatched stock comes back as a RETURN."""
    if inv.status != "ISSUED":
        raise BusinessError("Invoice is already cancelled")
    if inv.payments:
        raise BusinessError("Payments exist on this invoice - refund them before cancelling")
    if not reason.strip():
        raise BusinessError("Give a reason for the credit note")
    n = (db.scalar(select(func.count(CreditNote.id))) or 0) + 1
    cn = CreditNote(cn_no=f"CN/{inv.fy}/{n:04d}", invoice_id=inv.id, date=date.today(), amount_paise=inv.total_paise, reason=reason, created_by=user.id)
    db.add(cn)
    if inv.dispatched_at:
        from ..models import StockLedger
        for row in db.scalars(select(StockLedger).where(StockLedger.doc_type == "DISPATCH", StockLedger.doc_id == inv.id)).all():
            stock.post(db, row.item, -row.qty_base, -row.value_paise, "RETURN", doc_id=inv.id, doc_ref=cn.cn_no, user=user, note="Credit note " + cn.cn_no)
    inv.status = "CANCELLED"
    inv.order.status = "CANCELLED"
    db.flush()
    audit.log(db, user, "invoice", inv.id, "cancel", f"{cn.cn_no}: {reason}")
    return cn


def customer_outstanding(db, customer_id: int) -> int:
    invs = db.scalars(select(Invoice).where(Invoice.customer_id == customer_id, Invoice.status == "ISSUED")).all()
    return sum(outstanding(i) for i in invs)
