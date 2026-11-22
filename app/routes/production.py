from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from ..db import get_db
from ..errors import BusinessError
from ..models import Bom, Item, ProductionRun, Purchase, Supplier
from ..money import allowed_units, rupees_to_paise, to_base
from ..services import production as prod, purchasing, stock
from ..web import flash, redirect, render, require

router = APIRouter()
FLOOR = ("production", "admin")


def _on(s: str | None):
    return date.fromisoformat(s) if s else date.today()


# ---------------------------------------------------------------- purchases
@router.get("/purchases")
def purchases(request: Request, user=Depends(require(*FLOOR)), db=Depends(get_db)):
    items = db.scalars(select(Item).where(Item.kind != "FG", Item.active.is_(True)).order_by(Item.kind, Item.name)).all()
    return render(request, "purchases.html", user, items=items, units={i.id: allowed_units(i) for i in items},
                  suppliers=db.scalars(select(Supplier).order_by(Supplier.name)).all(),
                  purchases=db.scalars(select(Purchase).order_by(Purchase.id.desc()).limit(30)).all(), today=date.today())


@router.post("/purchases")
async def purchase_add(request: Request, user=Depends(require(*FLOOR)), db=Depends(get_db)):
    f = await request.form()
    lines = []
    for n in range(1, 6):
        if not f.get(f"item_{n}") or not (f.get(f"qty_{n}") or "").strip():
            continue
        item = db.get(Item, int(f[f"item_{n}"]))
        qty = to_base(item, f[f"qty_{n}"], f.get(f"unit_{n}"))
        rate = rupees_to_paise(f.get(f"rate_{n}") or "0")  # rate is always per the item's display unit (e.g. per kg)
        if rate <= 0:
            raise BusinessError(f"Enter the purchase rate for {item.name}")
        lines.append((item, qty, rate))
    p = purchasing.record_purchase(db, user, int(f["supplier_id"]), f.get("bill_no") or None, lines, _on(f.get("date")))
    db.commit(); flash(request, f"{p.ref} recorded. Raw-material stock updated.")
    return redirect("/purchases")


# --------------------------------------------------------------------- runs
@router.get("/runs")
def runs(request: Request, user=Depends(require(*FLOOR)), db=Depends(get_db)):
    rs = db.scalars(select(ProductionRun).order_by(ProductionRun.id.desc())).all()
    return render(request, "runs.html", user, runs=[prod.summary(db, r) for r in rs], plan=prod.plan_from_orders(db))


@router.get("/runs/new")
def run_new(request: Request, product_id: int = 0, user=Depends(require(*FLOOR)), db=Depends(get_db)):
    products = db.scalars(select(Bom).join(Item, Bom.product_id == Item.id).order_by(Item.name)).all()
    bom = db.scalar(select(Bom).where(Bom.product_id == product_id)) if product_id else (products[0] if len(products) == 1 else None)
    raw = [l for l in bom.lines if l.item.kind == "RAW"] if bom else []
    return render(request, "run_new.html", user, boms=products, bom=bom, raw=raw, units={l.item_id: allowed_units(l.item) for l in raw},
                  bals=stock.balances(db), today=date.today())


@router.post("/runs")
async def run_create(request: Request, user=Depends(require(*FLOOR)), db=Depends(get_db)):
    f = await request.form()
    pid, batches = int(f["product_id"]), int(f.get("batches") or 1)
    bom = prod.get_bom(db, pid)
    issues = {}
    for l in bom.lines:
        v = (f.get(f"issue_{l.item_id}") or "").strip()
        if l.item.kind == "RAW" and v:
            issues[l.item_id] = to_base(l.item, v, f.get(f"unit_{l.item_id}"))
    run = prod.start_run(db, user, pid, batches, issues, _on(f.get("date")), f.get("notes") or None)
    db.commit(); flash(request, f"{run.run_no} started. Raw materials issued from stock.")
    return redirect(f"/runs/{run.id}")


@router.get("/runs/{rid:int}")
def run_detail(rid: int, request: Request, user=Depends(require(*FLOOR)), db=Depends(get_db)):
    run = db.get(ProductionRun, rid)
    if not run:
        raise BusinessError("Run not found")
    bom = prod.get_bom(db, run.product_id)
    return render(request, "run_detail.html", user, s=prod.summary(db, run), run=run, packs=[l for l in bom.lines if l.item.kind == "PACK"], bom=bom)


@router.post("/runs/{rid:int}/complete")
async def run_complete(rid: int, request: Request, user=Depends(require(*FLOOR)), db=Depends(get_db)):
    f, run = await request.form(), db.get(ProductionRun, rid)
    bom = prod.get_bom(db, run.product_id)
    pack = {l.item_id: int(f[f"pack_{l.item_id}"]) for l in bom.lines if l.item.kind == "PACK" and (f.get(f"pack_{l.item_id}") or "").strip()}
    prod.complete_run(db, user, run, int(f.get("good") or 0), int(f.get("damaged") or 0), pack)
    db.commit(); flash(request, "Run completed. Finished-goods stock updated.")
    return redirect(f"/runs/{rid}")


@router.post("/runs/{rid:int}/overhead")
def run_overhead(rid: int, request: Request, kind: str = Form(...), amount: str = Form(...), note: str = Form(""),
                 user=Depends(require(*FLOOR)), db=Depends(get_db)):
    prod.add_overhead(db, user, db.get(ProductionRun, rid), kind, rupees_to_paise(amount), note or None)
    db.commit(); flash(request, "Overhead added to the run.")
    return redirect(f"/runs/{rid}")


# -------------------------------------------------------------------- stock
@router.get("/stock")
def stock_page(request: Request, user=Depends(require("production", "admin", "accountant", "owner")), db=Depends(get_db)):
    bals = stock.balances(db)
    items = db.scalars(select(Item).where(Item.active.is_(True)).order_by(Item.kind, Item.name)).all()
    return render(request, "stock.html", user, items=items, bals=bals, units={i.id: allowed_units(i) for i in items})


@router.post("/stock/adjust")
def stock_adjust(request: Request, item_id: int = Form(...), qty: str = Form(...), unit: str = Form(""), sign: str = Form("-"),
                 reason: str = Form(""), user=Depends(require(*FLOOR)), db=Depends(get_db)):
    item = db.get(Item, item_id)
    q = to_base(item, qty, unit or None) * (1 if sign == "+" else -1)
    stock.adjust(db, user, item, q, reason)
    db.commit(); flash(request, "Adjustment posted to the stock ledger.")
    return redirect("/stock")
