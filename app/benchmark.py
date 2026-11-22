"""Runs the Section 5 scenario through the service layer and compares with the expected results."""
from decimal import Decimal

from sqlalchemy import select

from .errors import BusinessError
from .models import Customer, Item, Supplier, User
from .money import to_base
from .services import production, purchasing, sales, stock


def run_benchmark(db) -> list[dict]:
    u = lambda name: db.scalar(select(User).where(User.username == name))
    item = lambda code: db.scalar(select(Item).where(Item.code == code))
    cust = lambda name: db.scalar(select(Customer).where(Customer.name == name))
    admin, prod, sa, sb = u("admin"), u("production"), u("salesman_a"), u("salesman_b")
    flour, oil, spice, pouch, carton, mur = (item(c) for c in ("FLOUR", "OIL", "SPICE", "POUCH200", "CARTON20", "MUR200"))
    bal = lambda i: stock.balance(db, i.id)[0]
    res = []
    check = lambda label, expected, actual: res.append({"check": label, "expected": str(expected), "actual": str(actual), "ok": str(expected) == str(actual)})

    # 1. purchase 60 kg flour @ Rs 45
    sup = db.scalar(select(Supplier))
    purchasing.record_purchase(db, prod, sup.id, "B-101", [(flour, to_base(flour, 60), 4500)])
    # 2. production run: issue 40 kg flour, 10.5 L oil, 1.2 kg spice
    run = production.start_run(db, prod, mur.id, 1, {flour.id: to_base(flour, 40), oil.id: to_base(oil, "10.5"), spice.id: to_base(spice, "1.2")})
    # 3. actual output 188, 6 pouches damaged, 9 cartons used
    production.complete_run(db, prod, run, good=188, damaged=6, pack_actuals={carton.id: 9})
    check("Flour closing balance (kg)", 120, bal(flour) // 1000)
    check("Oil closing balance (L)", "19.5", Decimal(bal(oil)) / 1000)
    check("Pouches closing balance", 306, bal(pouch))
    check("Finished goods after run", 238, bal(mur))
    # 4-5. Retailer A: 150 packets -> approve -> invoice (CGST+SGST) -> dispatch
    oa = sales.create_order(db, sa, cust("Retailer A").id, [(mur, 150)])
    sales.approve(db, admin, oa)
    inv = sales.create_invoice(db, admin, oa)
    sales.dispatch(db, admin, inv)
    check("Finished goods after Retailer A dispatch", 88, bal(mur))
    check("Retailer A invoice taxable (paise)", 750000, inv.taxable_paise)
    check("Retailer A CGST (paise)", 18750, inv.cgst_paise)
    check("Retailer A SGST (paise)", 18750, inv.sgst_paise)
    check("Retailer A invoice total (paise)", 787500, inv.total_paise)
    # 6. Distributor B: 120 packets -> shortage flagged, IGST shown
    ob = sales.create_order(db, sb, cust("Distributor B").id, [(mur, 120)])
    shortage = sum(c["shortage"] for c in sales.stock_check(db, ob))
    try:
        sales.approve(db, admin, ob)
        blocked = False
    except BusinessError:
        blocked = True
    check("Distributor B shortage (packets)", 32, shortage)
    check("Distributor B approval blocked by shortage", True, blocked)
    _, tot, intra = sales.tax_preview(ob)
    check("Distributor B uses IGST (paise / intra-state)", "30000/False", f"{tot['igst']}/{intra}")
    # 7. payment Rs 4,000 from Retailer A
    sales.record_payment(db, admin, inv, 400000, "upi", "UPI-1")
    check("Retailer A outstanding (paise)", 387500, sales.outstanding(inv))
    # 8. overheads then cost per packet
    production.add_overhead(db, prod, run, "labour", 120000)
    production.add_overhead(db, prod, run, "gas/power", 60000)
    s = production.summary(db, run)
    oil_line = next(l for l in s["lines"] if l["item"].code == "OIL")
    check("Yield variance %", "-6.00", s["yield_var"])
    check("Yield flagged", True, s["yield_flag"])
    check("Oil variance %", "5.00", oil_line["var_pct"])
    check("Oil variance flagged", True, oil_line["flag"])
    check("Material cost (paise)", 405500, s["material"])
    check("Overheads (paise)", 180000, s["overhead"])
    check("Cost per packet (Rs)", "31.14", s["cost_per_packet"])
    db.commit()
    return res


if __name__ == "__main__":
    from .db import Base, SessionLocal, engine
    from .seed import seed
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
        rows = run_benchmark(db)
    w = max(len(r["check"]) for r in rows)
    for r in rows:
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']:<{w}}  expected={r['expected']:<10} actual={r['actual']}")
    raise SystemExit(0 if all(r["ok"] for r in rows) else 1)
