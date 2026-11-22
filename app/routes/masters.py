from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from ..db import get_db
from ..errors import BusinessError
from ..models import Bom, BomLine, Customer, Item, Supplier
from ..money import allowed_units, rupees_to_paise, to_base
from ..services import audit
from ..web import flash, redirect, render, require

router = APIRouter()
UNIT_SETUP = {"kg": ("g", "kg", 1000), "L": ("ml", "L", 1000), "pcs": ("pcs", "pcs", 1), "packet": ("pcs", "packet", 1)}


@router.get("/customers")
def customers(request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    return render(request, "customers.html", user, rows=db.scalars(select(Customer).order_by(Customer.name)).all(),
                  suppliers=db.scalars(select(Supplier).order_by(Supplier.name)).all())


@router.post("/customers")
def customer_add(request: Request, name: str = Form(...), kind: str = Form("retailer"), gstin: str = Form(""), state: str = Form(...),
                 phone: str = Form(""), address: str = Form(""), user=Depends(require("admin")), db=Depends(get_db)):
    c = Customer(name=name.strip(), kind=kind, gstin=gstin.strip().upper() or None, state=state.strip(), phone=phone or None, address=address or None)
    db.add(c); db.flush()
    audit.log(db, user, "customer", c.id, "create", c.name)
    db.commit(); flash(request, "Customer added.")
    return redirect("/customers")


@router.post("/suppliers")
def supplier_add(request: Request, name: str = Form(...), gstin: str = Form(""), phone: str = Form(""),
                 user=Depends(require("admin", "production")), db=Depends(get_db)):
    db.add(Supplier(name=name.strip(), gstin=gstin.strip().upper() or None, phone=phone or None))
    db.commit(); flash(request, "Supplier added.")
    return redirect("/customers")


@router.get("/items")
def items(request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    return render(request, "items.html", user, rows=db.scalars(select(Item).order_by(Item.kind, Item.name)).all())


@router.post("/items")
def item_add(request: Request, code: str = Form(...), name: str = Form(...), kind: str = Form(...), unit: str = Form(...), hsn: str = Form(""),
             gst: str = Form("0"), price: str = Form("0"), per_carton: str = Form(""), shelf_life: str = Form(""), reorder: str = Form("0"),
             user=Depends(require("admin")), db=Depends(get_db)):
    base, disp, factor = UNIT_SETUP[unit]
    it = Item(code=code.strip().upper(), name=name.strip(), kind=kind, base_unit=base, display_unit=disp, factor=factor, hsn=hsn or None,
              gst_bp=int(round(float(gst or 0) * 100)), price_paise=rupees_to_paise(price or 0) if kind == "FG" else 0,
              units_per_carton=int(per_carton) if per_carton else None, shelf_life_days=int(shelf_life) if shelf_life else None)
    db.add(it); db.flush()
    it.reorder_base = to_base(it, reorder or 0)
    audit.log(db, user, "item", it.id, "create", it.name)
    db.commit(); flash(request, "Item added.")
    return redirect("/items")


@router.post("/items/{iid:int}/price")
def item_price(iid: int, request: Request, price: str = Form(...), gst: str = Form(...), user=Depends(require("admin")), db=Depends(get_db)):
    it = db.get(Item, iid)
    old = (it.price_paise, it.gst_bp)
    it.price_paise, it.gst_bp = rupees_to_paise(price), int(round(float(gst) * 100))
    audit.log(db, user, "item", it.id, "price", f"{old} -> {(it.price_paise, it.gst_bp)}")
    db.commit(); flash(request, "Price updated. Posted invoices keep the price they were billed at.")
    return redirect("/items")


# ---------------------------------------------------------------------- BOM
@router.get("/bom")
def bom_page(request: Request, user=Depends(require("admin", "production")), db=Depends(get_db)):
    boms = db.scalars(select(Bom)).all()
    products = db.scalars(select(Item).where(Item.kind == "FG").order_by(Item.name)).all()
    inputs = db.scalars(select(Item).where(Item.kind != "FG").order_by(Item.kind, Item.name)).all()
    return render(request, "bom.html", user, boms=boms, no_bom=[p for p in products if p.id not in {b.product_id for b in boms}],
                  inputs=inputs, units={i.id: allowed_units(i) for i in inputs})


@router.post("/bom/create")
def bom_create(request: Request, product_id: int = Form(...), yield_packets: int = Form(...), user=Depends(require("admin")), db=Depends(get_db)):
    if yield_packets < 1:
        raise BusinessError("Yield must be at least 1 packet per batch")
    db.add(Bom(product_id=product_id, yield_packets=yield_packets)); db.commit()
    return redirect("/bom")


@router.post("/bom/{bid:int}/yield")
def bom_yield(bid: int, request: Request, yield_packets: int = Form(...), user=Depends(require("admin")), db=Depends(get_db)):
    b = db.get(Bom, bid)
    audit.log(db, user, "bom", bid, "yield", f"{b.yield_packets} -> {yield_packets}")
    b.yield_packets = yield_packets; db.commit(); flash(request, "Yield updated.")
    return redirect("/bom")


@router.post("/bom/{bid:int}/line")
def bom_line(bid: int, request: Request, item_id: int = Form(...), qty: str = Form(...), unit: str = Form(""), user=Depends(require("admin")), db=Depends(get_db)):
    b, item = db.get(Bom, bid), db.get(Item, item_id)
    q = to_base(item, qty, unit or None)
    line = next((l for l in b.lines if l.item_id == item_id), None)
    if line:
        line.qty_base = q
    else:
        b.lines.append(BomLine(item_id=item_id, qty_base=q))
    audit.log(db, user, "bom", bid, "line", f"{item.name} = {q}")
    db.commit(); flash(request, "Recipe updated.")
    return redirect("/bom")


@router.post("/bom/{bid:int}/line/{lid:int}/delete")
def bom_line_delete(bid: int, lid: int, request: Request, user=Depends(require("admin")), db=Depends(get_db)):
    l = db.get(BomLine, lid)
    db.delete(l); audit.log(db, user, "bom", bid, "line-delete", l.item.name); db.commit()
    return redirect("/bom")
