"""Auditor reports. Each returns {"title", "sections": [{"name", "headers", "rows"}]} of display strings,
so the same data feeds the HTML page, CSV, Excel and PDF exports."""
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from .models import (AuditLog, CreditNote, Customer, Invoice, InvoiceLine, Item, Order, Payment, ProductionRun,
                     Purchase, PurchaseLine, StockLedger)
from .money import fmt_qty, paise_str, pct_var
from .services import production, sales, stock

P = paise_str
RECEIPT = ("PURCHASE", "RUN_OUTPUT", "RETURN")
ISSUE = ("RUN_ISSUE", "RUN_PACK", "DISPATCH")
KIND_NAME = {"RAW": "Raw materials", "PACK": "Packaging materials", "FG": "Finished goods"}


def _sec(name, headers, rows):
    return {"name": name, "headers": headers, "rows": rows}


def _invoices(db, a, b, issued_only=True):
    q = select(Invoice).where(Invoice.date >= a, Invoice.date <= b).order_by(Invoice.date, Invoice.seq)
    if issued_only:
        q = q.where(Invoice.status == "ISSUED")
    return db.scalars(q).all()


# ------------------------------------------------------------------ sales
def sales_register(db, a, b, **_):
    invs = _invoices(db, a, b, issued_only=False)
    rows = [[i.inv_no, str(i.date), i.customer.name, i.customer.gstin or "-", i.place_of_supply, P(i.taxable_paise),
             P(i.cgst_paise), P(i.sgst_paise), P(i.igst_paise), P(i.total_paise), i.status.title()] for i in invs]
    ok = [i for i in invs if i.status == "ISSUED"]
    rows.append(["Total (excluding cancelled)", "", "", "", "", P(sum(i.taxable_paise for i in ok)), P(sum(i.cgst_paise for i in ok)),
                 P(sum(i.sgst_paise for i in ok)), P(sum(i.igst_paise for i in ok)), P(sum(i.total_paise for i in ok)), ""])
    cns = db.scalars(select(CreditNote).where(CreditNote.date >= a, CreditNote.date <= b)).all()
    return {"title": "Sales register", "sections": [
        _sec("Tax invoices", ["Invoice", "Date", "Customer", "GSTIN", "Place of supply", "Taxable", "CGST", "SGST", "IGST", "Total", "Status"], rows),
        _sec("Credit notes", ["Credit note", "Date", "Against invoice", "Amount", "Reason"],
             [[c.cn_no, str(c.date), c.invoice.inv_no, P(c.amount_paise), c.reason] for c in cns])]}


def customer_sales(db, a, b, **_):
    agg = defaultdict(lambda: [0, 0, 0])
    for i in _invoices(db, a, b):
        r = agg[i.customer.name]; r[0] += 1; r[1] += i.taxable_paise; r[2] += i.total_paise
    rows = [[n, r[0], P(r[1]), P(r[2] - r[1]), P(r[2])] for n, r in sorted(agg.items())]
    return {"title": "Customer-wise sales", "sections": [_sec("", ["Customer", "Invoices", "Taxable", "GST", "Total"], rows)]}


def product_sales(db, a, b, **_):
    agg = defaultdict(lambda: [0, 0, 0, None])
    for i in _invoices(db, a, b):
        for l in i.lines:
            r = agg[l.description]; r[0] += l.qty; r[1] += l.taxable_paise
            r[2] += l.cgst_paise + l.sgst_paise + l.igst_paise; r[3] = l.hsn
    rows = [[n, r[3] or "-", r[0], P(r[1]), P(r[2]), P(r[1] + r[2])] for n, r in sorted(agg.items())]
    return {"title": "Product-wise sales", "sections": [_sec("", ["Product", "HSN", "Packets", "Taxable", "GST", "Total"], rows)]}


def gst_summary(db, a, b, **_):
    invs = _invoices(db, a, b)
    tot = [sum(getattr(i, f) for i in invs) for f in ("taxable_paise", "igst_paise", "cgst_paise", "sgst_paise")]
    b2b, rate, hsn = [], defaultdict(lambda: [0, 0, 0, 0]), defaultdict(lambda: [0, 0, 0, ""])
    for i in invs:
        per = defaultdict(lambda: [0, 0, 0, 0])
        for l in i.lines:
            p = per[l.gst_bp]; p[0] += l.taxable_paise; p[1] += l.igst_paise; p[2] += l.cgst_paise; p[3] += l.sgst_paise
            h = hsn[(l.hsn, l.description)]; h[0] += l.qty; h[1] += l.taxable_paise; h[2] += l.cgst_paise + l.sgst_paise + l.igst_paise
            r = rate[l.gst_bp]
            r[0] += l.taxable_paise; r[1] += l.igst_paise; r[2] += l.cgst_paise; r[3] += l.sgst_paise
        for bp, p in per.items():
            b2b.append([i.customer.gstin or "Unregistered", i.inv_no, str(i.date), P(i.total_paise), i.place_of_supply,
                        f"{bp / 100:g}%", P(p[0]), P(p[1]), P(p[2]), P(p[3])])
    cns = db.scalars(select(CreditNote).where(CreditNote.date >= a, CreditNote.date <= b)).all()
    return {"title": "GST summary of outward supplies", "sections": [
        _sec("GSTR-3B 3.1(a): outward taxable supplies (other than zero rated, nil rated and exempted)",
             ["Taxable value", "Integrated tax", "Central tax", "State/UT tax", "Cess"], [[P(tot[0]), P(tot[1]), P(tot[2]), P(tot[3]), "0.00"]]),
        _sec("GSTR-1 table 4: B2B invoices", ["Recipient GSTIN", "Invoice", "Date", "Invoice value", "Place of supply", "Rate", "Taxable", "IGST", "CGST", "SGST"], b2b),
        _sec("Rate-wise summary", ["Rate", "Taxable", "IGST", "CGST", "SGST"], [[f"{k / 100:g}%"] + [P(x) for x in v] for k, v in sorted(rate.items())]),
        _sec("GSTR-1 table 12: HSN summary", ["HSN", "Description", "UQC", "Quantity", "Taxable", "Total tax"],
             [[h or "-", d, "PCS", v[0], P(v[1]), P(v[2])] for (h, d), v in sorted(hsn.items(), key=lambda x: str(x[0]))]),
        _sec("GSTR-1 table 9B: credit notes issued (their invoices are excluded above)", ["Credit note", "Date", "Invoice", "Value"],
             [[c.cn_no, str(c.date), c.invoice.inv_no, P(c.amount_paise)] for c in cns])]}


# --------------------------------------------------------------- purchases
def purchase_register(db, a, b, **_):
    rows, total = [], 0
    for l in db.scalars(select(PurchaseLine).join(Purchase).where(Purchase.date >= a, Purchase.date <= b).order_by(Purchase.date, Purchase.id)):
        p = l.purchase; total += l.amount_paise
        rows.append([p.ref, str(p.date), p.supplier.name, p.bill_no or "-", l.item.name, fmt_qty(l.item, l.qty_base), l.item.display_unit, P(l.rate_paise), P(l.amount_paise)])
    rows.append(["Total", "", "", "", "", "", "", "", P(total)])
    return {"title": "Purchase register", "sections": [_sec("", ["Ref", "Date", "Supplier", "Bill no", "Item", "Qty", "Unit", "Rate", "Amount"], rows)]}


# ------------------------------------------------------------------- stock
def stock_summary(db, a, b, **_):
    rows_by_item = defaultdict(list)
    for r in db.scalars(select(StockLedger).where(StockLedger.entry_date <= b)):
        rows_by_item[r.item_id].append(r)
    sections = []
    for kind in ("RAW", "PACK", "FG"):
        out = []
        for it in db.scalars(select(Item).where(Item.kind == kind).order_by(Item.name)):
            rs = rows_by_item.get(it.id, [])
            opening = sum(r.qty_base for r in rs if r.entry_date < a or r.doc_type == "OPENING")
            inp = [r for r in rs if r.entry_date >= a and r.doc_type != "OPENING"]
            rec = sum(r.qty_base for r in inp if r.doc_type in RECEIPT)
            iss = -sum(r.qty_base for r in inp if r.doc_type in ISSUE)
            adj = sum(r.qty_base for r in inp if r.doc_type == "ADJUST")
            close, val = sum(r.qty_base for r in rs), sum(r.value_paise for r in rs)
            f = lambda x: fmt_qty(it, x)
            out.append([it.name, it.display_unit, f(opening), f(rec), f(iss), f(adj), f(close), P(val)])
        sections.append(_sec(KIND_NAME[kind], ["Item", "Unit", "Opening", "Receipts", "Issues / sales", "Adjustments", "Closing", "Closing value"], out))
    return {"title": "Stock summary", "sections": sections}


def stock_ledger(db, a, b, item_id=None, **_):
    items = [db.get(Item, item_id)] if item_id else db.scalars(select(Item).order_by(Item.kind, Item.name)).all()
    sections = []
    for it in items:
        rows_all = db.scalars(select(StockLedger).where(StockLedger.item_id == it.id, StockLedger.entry_date <= b).order_by(StockLedger.entry_date, StockLedger.id)).all()
        run, out = 0, []
        for r in rows_all:
            run += r.qty_base
            if r.entry_date >= a or r.doc_type == "OPENING":
                out.append([str(r.entry_date), r.doc_type.replace("_", " ").title(), r.doc_ref or "-", r.batch_no or "-",
                            fmt_qty(it, r.qty_base) if r.qty_base > 0 else "", fmt_qty(it, -r.qty_base) if r.qty_base < 0 else "",
                            fmt_qty(it, run), P(r.value_paise), r.user.full_name if r.user else "-", r.note or ""])
        if out:
            sections.append(_sec(f"{it.name} ({it.display_unit})", ["Date", "Movement", "Source document", "Batch", "In", "Out", "Balance", "Value", "By", "Note"], out))
    return {"title": "Stock ledger", "sections": sections}


# -------------------------------------------------------------- production
def production_report(db, a, b, **_):
    runs = db.scalars(select(ProductionRun).where(ProductionRun.date >= a, ProductionRun.date <= b).order_by(ProductionRun.id)).all()
    head, det = [], []
    for r in runs:
        s = production.summary(db, r)
        head.append([r.run_no, str(r.date), r.product.name, r.batch_no or "-", r.expected_output, r.good_output if r.good_output is not None else "-",
                     f"{s['yield_var']:+}%" if s["yield_var"] is not None else "-", "FLAG" if s["yield_flag"] else "", r.wastage_packets, r.status.title()])
        for l in s["lines"]:
            det.append([r.run_no, l["item"].name, l["item"].display_unit, fmt_qty(l["item"], l["planned"]), fmt_qty(l["item"], l["actual"]),
                        f"{l['var_pct']:+}%", "FLAG" if l["flag"] else ""])
    return {"title": "Production report: planned vs actual", "sections": [
        _sec("Yield and wastage", ["Run", "Date", "Product", "Batch", "Expected packets", "Actual good", "Yield variance", "Alert", "Wasted packets", "Status"], head),
        _sec("Consumption: planned vs actual", ["Run", "Material", "Unit", "Planned", "Actual", "Variance", "Alert"], det)]}


def cost_report(db, a, b, **_):
    rows = []
    for r in db.scalars(select(ProductionRun).where(ProductionRun.date >= a, ProductionRun.date <= b, ProductionRun.status == "COMPLETED")):
        s = production.summary(db, r)
        rows.append([r.run_no, r.product.name, r.good_output, P(s["material"]), P(s["overhead"]), P(s["total"]), s["cost_per_packet"],
                     P(r.product.price_paise), s["margin"], f"{s['margin_pct']}%" if s["margin_pct"] is not None else "-"])
    return {"title": "Cost per packet and margin", "sections": [_sec(
        "Weighted-average material cost + run overheads, divided by good packets",
        ["Run", "Product", "Good packets", "Materials", "Overheads", "Total cost", "Cost / packet", "Selling price", "Margin / packet", "Margin %"], rows)]}


# ----------------------------------------------------------------- receivables
def collections(db, a, b, **_):
    ps = db.scalars(select(Payment).where(Payment.date >= a, Payment.date <= b).order_by(Payment.date, Payment.id)).all()
    rows = [[str(p.date), p.customer.name, p.invoice.inv_no, p.mode, p.ref or "-", P(p.amount_paise)] for p in ps]
    rows.append(["Total", "", "", "", "", P(sum(p.amount_paise for p in ps))])
    return {"title": "Payments and collections", "sections": [_sec("", ["Date", "Customer", "Invoice", "Mode", "Reference", "Amount"], rows)]}


def outstanding(db, a, b, **_):
    cust, detail = defaultdict(lambda: [0, 0, 0, 0, 0]), []
    for i in db.scalars(select(Invoice).where(Invoice.status == "ISSUED", Invoice.date <= b).order_by(Invoice.date)):
        due = i.total_paise - sum(p.amount_paise for p in i.payments if p.date <= b)
        if due <= 0:
            continue
        age = (b - i.date).days
        k = 0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3
        c = cust[i.customer.name]; c[k] += due; c[4] += due
        detail.append([i.customer.name, i.inv_no, str(i.date), age, P(i.total_paise), P(i.total_paise - due), P(due)])
    rows = [[n] + [P(x) for x in v] for n, v in sorted(cust.items())]
    rows.append(["Total"] + [P(sum(v[k] for v in cust.values())) for k in range(5)])
    return {"title": f"Outstanding receivables with ageing as of {b}", "sections": [
        _sec("By customer", ["Customer", "0-30 days", "31-60 days", "61-90 days", "Over 90 days", "Total due"], rows),
        _sec("Invoice-wise", ["Customer", "Invoice", "Date", "Age (days)", "Invoice total", "Paid", "Due"], detail)]}


def audit_trail(db, a, b, **_):
    q = select(AuditLog).where(AuditLog.ts >= datetime.combine(a, datetime.min.time()), AuditLog.ts < datetime.combine(b + timedelta(days=1), datetime.min.time())).order_by(AuditLog.id)
    return {"title": "Audit trail", "sections": [_sec("", ["Time", "User", "Entity", "Id", "Action", "Detail"],
            [[str(l.ts), l.user.full_name if l.user else "-", l.entity, l.entity_id, l.action, l.detail or ""] for l in db.scalars(q)])]}


REPORTS = {  # key: (function, label, which roles-groups see it is handled in routes)
    "sales-register": (sales_register, "Sales register"), "customer-sales": (customer_sales, "Customer-wise sales"),
    "product-sales": (product_sales, "Product-wise sales"), "gst-summary": (gst_summary, "GST summary (GSTR-1 / 3B)"),
    "purchase-register": (purchase_register, "Purchase register"), "stock-summary": (stock_summary, "Stock summary"),
    "stock-ledger": (stock_ledger, "Stock ledger"), "production": (production_report, "Production: planned vs actual"),
    "cost": (cost_report, "Cost per packet and margin"), "collections": (collections, "Payments and collections"),
    "outstanding": (outstanding, "Outstanding and ageing"), "audit": (audit_trail, "Audit trail"),
}


# --------------------------------------------------------------- dashboard
def dashboard(db) -> dict:
    today = date.today()
    bals = stock.balances(db)
    items = db.scalars(select(Item).where(Item.active.is_(True)).order_by(Item.kind, Item.name)).all()
    stock_rows = [{"item": i, "qty": bals.get(i.id, (0, 0))[0], "low": bals.get(i.id, (0, 0))[0] <= i.reorder_base and i.kind != "FG" and i.reorder_base > 0} for i in items]
    runs = db.scalars(select(ProductionRun).where(ProductionRun.status == "COMPLETED").order_by(ProductionRun.id.desc()).limit(10)).all()
    sums = [production.summary(db, r) for r in runs]
    latest_cost = {}
    for s in sums:
        latest_cost.setdefault(s["run"].product.name, s)
    due = sum(sales.customer_outstanding(db, c.id) for c in db.scalars(select(Customer)))
    return {
        "orders_today": db.scalar(select(func.count(Order.id)).where(Order.created_at >= datetime.combine(today, datetime.min.time()))),
        "pending": db.scalar(select(func.count(Order.id)).where(Order.status == "SUBMITTED")),
        "sales_month": db.scalar(select(func.coalesce(func.sum(Invoice.total_paise), 0)).where(Invoice.status == "ISSUED", Invoice.date >= today.replace(day=1))),
        "stock": stock_rows, "low": [r for r in stock_rows if r["low"]],
        "alerts": [s for s in sums if s["alerts"]], "costs": list(latest_cost.values()), "outstanding": due,
        "plan": production.plan_from_orders(db),
    }
