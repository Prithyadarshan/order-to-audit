import json
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from .. import exports, integrations
from ..config import FACTORY
from ..db import get_db
from ..errors import BusinessError
from ..models import Customer, Invoice, Item, Order, StockLedger
from ..money import allowed_units, inr, rupees_to_paise, to_base, paise_str
from ..services import sales
from ..web import flash, redirect, render, require

router = APIRouter()
SELL = ("salesman", "admin")
VIEW_INV = ("admin", "accountant", "owner")


def _own(user, order):
    if user.role == "salesman" and order.salesman_id != user.id:
        raise BusinessError("That order belongs to another salesman")


@router.get("/orders")
def orders(request: Request, status: str = "", user=Depends(require(*SELL)), db=Depends(get_db)):
    q = select(Order).order_by(Order.id.desc())
    if user.role == "salesman":
        q = q.where(Order.salesman_id == user.id)
    if status:
        q = q.where(Order.status == status.upper())
    return render(request, "orders.html", user, orders=db.scalars(q).all(), status=status.upper())


@router.get("/orders/new")
def order_new(request: Request, user=Depends(require(*SELL)), db=Depends(get_db)):
    customers = [{"id": c.id, "name": c.name, "kind": c.kind} for c in db.scalars(select(Customer).where(Customer.active.is_(True)).order_by(Customer.name))]
    products = [{"id": i.id, "name": i.name, "price": paise_str(i.price_paise), "units": allowed_units(i)}
                for i in db.scalars(select(Item).where(Item.kind == "FG", Item.active.is_(True)).order_by(Item.name))]
    return render(request, "order_new.html", user, customers=customers, products=products)


@router.post("/api/orders")
async def api_orders(request: Request, user=Depends(require(*SELL)), db=Depends(get_db)):
    data = await request.json()
    lines = []
    for l in data.get("lines", []):
        item = db.get(Item, int(l["item_id"]))
        if not item:
            raise BusinessError("Unknown product")
        lines.append((item, to_base(item, l["qty"], l.get("unit"))))
    order = sales.create_order(db, user, int(data["customer_id"]), lines, (data.get("note") or "")[:200] or None, data.get("client_ref"))
    db.commit()
    return JSONResponse({"ok": True, "id": order.id, "order_no": order.order_no, "total": inr(order.total_paise)})


@router.get("/orders/{oid:int}")
def order_detail(oid: int, request: Request, user=Depends(require(*SELL)), db=Depends(get_db)):
    order = db.get(Order, oid)
    if not order:
        raise BusinessError("Order not found")
    _own(user, order)
    lines, tot, intra = sales.tax_preview(order)
    check = sales.stock_check(db, order) if user.role == "admin" and order.status in ("SUBMITTED", "APPROVED") else None
    return render(request, "order_detail.html", user, order=order, tax=tot, intra=intra, check=check,
                  short=bool(check and any(c["shortage"] for c in check)))


def _admin_order(db, oid):
    o = db.get(Order, oid)
    if not o:
        raise BusinessError("Order not found")
    return o


@router.post("/orders/{oid:int}/edit")
async def order_edit(oid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    o, form = _admin_order(db, oid), await request.form()
    changes = {l.id: (int(form.get(f"qty_{l.id}", l.qty) or 0), rupees_to_paise(form.get(f"price_{l.id}", "0") or "0")) for l in o.lines}
    sales.edit_lines(db, user, o, changes)
    db.commit(); flash(request, "Order updated. Approve it again to continue.")
    return redirect(f"/orders/{oid}")


@router.post("/orders/{oid:int}/reduce")
def order_reduce(oid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    sales.reduce_to_available(db, user, _admin_order(db, oid))
    db.commit(); flash(request, "Quantities reduced to what is in stock.")
    return redirect(f"/orders/{oid}")


@router.post("/orders/{oid:int}/approve")
def order_approve(oid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    sales.approve(db, user, _admin_order(db, oid))
    db.commit(); flash(request, "Order approved.")
    return redirect(f"/orders/{oid}")


@router.post("/orders/{oid:int}/reject")
def order_reject(oid: int, request: Request, reason: str = Form(""), user=Depends(require("admin")), db=Depends(get_db)):
    sales.reject(db, user, _admin_order(db, oid), reason)
    db.commit(); flash(request, "Order rejected.")
    return redirect(f"/orders/{oid}")


@router.post("/orders/{oid:int}/invoice")
def order_invoice(oid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    inv = sales.create_invoice(db, user, _admin_order(db, oid))
    db.commit(); flash(request, f"Invoice {inv.inv_no} raised.")
    return redirect(f"/invoices/{inv.id}")


@router.get("/my-sales")
def my_sales(request: Request, user=Depends(require("salesman")), db=Depends(get_db)):
    mine = db.scalars(select(Order).where(Order.salesman_id == user.id, Order.status.notin_(("REJECTED", "CANCELLED"))).order_by(Order.id.desc())).all()
    by = {}
    for o in mine:
        r = by.setdefault(o.customer.name, {"orders": 0, "value": 0, "last": o.created_at})
        r["orders"] += 1; r["value"] += o.total_paise
    return render(request, "my_sales.html", user, rows=sorted(by.items()), total=sum(r["value"] for r in by.values()))


# ----------------------------------------------------------------- invoices
@router.get("/invoices")
def invoices(request: Request, user=Depends(require(*VIEW_INV)), db=Depends(get_db)):
    invs = db.scalars(select(Invoice).order_by(Invoice.id.desc())).all()
    return render(request, "invoices.html", user, invs=invs, due=sales.outstanding)


@router.get("/invoices/{iid:int}")
def invoice_detail(iid: int, request: Request, user=Depends(require(*VIEW_INV)), db=Depends(get_db)):
    inv = db.get(Invoice, iid)
    if not inv:
        raise BusinessError("Invoice not found")
    link = str(request.base_url) + f"invoices/{inv.id}/pdf"
    text = f"Invoice {inv.inv_no} from {FACTORY['name']} for {inv.customer.name}: {inv_total(inv)}. Download: {link}"
    batches = db.scalars(select(StockLedger).where(StockLedger.doc_type == "DISPATCH", StockLedger.doc_id == inv.id)).all()
    return render(request, "invoice_detail.html", user, inv=inv, due=sales.outstanding(inv), paid=sales.paid(inv), batches=batches,
                  wa=f"https://wa.me/?text={quote(text)}", mail=f"mailto:?subject={quote('Invoice ' + inv.inv_no)}&body={quote(text)}")


def inv_total(inv):
    return inr(inv.total_paise)


@router.get("/invoices/{iid:int}/pdf")
def invoice_pdf(iid: int, user=Depends(require(*VIEW_INV)), db=Depends(get_db)):
    inv = db.get(Invoice, iid)
    if not inv:
        raise BusinessError("Invoice not found")
    return Response(exports.invoice_pdf(inv), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv.inv_no.replace("/", "-")}.pdf"'})


@router.post("/invoices/{iid:int}/dispatch")
def inv_dispatch(iid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    sales.dispatch(db, user, db.get(Invoice, iid))
    db.commit(); flash(request, "Dispatched. Finished-goods stock reduced.")
    return redirect(f"/invoices/{iid}")


@router.post("/invoices/{iid:int}/payment")
def inv_payment(iid: int, request: Request, amount: str = Form(...), mode: str = Form("cash"), ref: str = Form(""),
                user=Depends(require("admin")), db=Depends(get_db)):
    sales.record_payment(db, user, db.get(Invoice, iid), rupees_to_paise(amount), mode, ref or None)
    db.commit(); flash(request, "Payment recorded.")
    return redirect(f"/invoices/{iid}")


@router.post("/invoices/{iid:int}/cancel")
def inv_cancel(iid: int, request: Request, reason: str = Form(""), user=Depends(require("admin")), db=Depends(get_db)):
    cn = sales.cancel_invoice(db, user, db.get(Invoice, iid), reason)
    db.commit(); flash(request, f"Invoice cancelled with credit note {cn.cn_no}.")
    return redirect(f"/invoices/{iid}")


@router.get("/invoices/{iid:int}/einvoice.json")
def invoice_einvoice(iid: int, user=Depends(require(*VIEW_INV)), db=Depends(get_db)):
    inv = db.get(Invoice, iid)
    if not inv:
        raise BusinessError("Invoice not found")
    return Response(json.dumps(integrations.einvoice_json(inv), indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="einvoice-{inv.inv_no.replace("/", "-")}.json"'})


@router.get("/invoices/{iid:int}/ewaybill.json")
def invoice_ewaybill(iid: int, vehicle: str = "TN00AA0000", km: int = 0, user=Depends(require(*VIEW_INV)), db=Depends(get_db)):
    inv = db.get(Invoice, iid)
    if not inv:
        raise BusinessError("Invoice not found")
    return Response(json.dumps(integrations.ewaybill_json(inv, vehicle, km), indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="ewaybill-{inv.inv_no.replace("/", "-")}.json"'})
