"""Loads the Section 5 master data. `python -m app.seed` (add --reset to wipe first)."""
import sys
from datetime import date

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import FG, PACK, RAW, Bom, BomLine, Customer, Item, Supplier, User
from .security import hash_password
from .services import stock

USERS = [  # username, full name, role, password
    ("salesman_a", "Salesman A", "salesman", "sales123"),
    ("salesman_b", "Salesman B", "salesman", "sales123"),
    ("admin", "Factory Admin", "admin", "admin123"),
    ("production", "Production Supervisor", "production", "prod123"),
    ("accountant", "Accountant", "accountant", "acct123"),
    ("owner", "Owner", "owner", "owner123"),
]


def seed(db, on: date | None = None):
    if db.scalar(select(User.id).limit(1)):
        return
    on = on or date.today().replace(day=1)  # opening balances sit on the 1st of the month
    for u, n, r, pw in USERS:
        db.add(User(username=u, full_name=n, role=r, pw_hash=hash_password(pw)))
    mk = lambda **k: Item(**k)
    flour = mk(code="FLOUR", name="Flour", kind=RAW, base_unit="g", display_unit="kg", factor=1000, reorder_base=30000)
    oil = mk(code="OIL", name="Cooking oil", kind=RAW, base_unit="ml", display_unit="L", factor=1000, reorder_base=10000)
    spice = mk(code="SPICE", name="Salt and spice mix", kind=RAW, base_unit="g", display_unit="kg", factor=1000, reorder_base=2000)
    pouch = mk(code="POUCH200", name="200 g pouch", kind=PACK, base_unit="pcs", display_unit="pcs", factor=1, reorder_base=200)
    carton = mk(code="CARTON20", name="Carton (holds 20 packets)", kind=PACK, base_unit="pcs", display_unit="pcs", factor=1, reorder_base=10)
    mur = mk(code="MUR200", name="Murukku 200 g", kind=FG, base_unit="pcs", display_unit="packet", factor=1, hsn="1905",
             gst_bp=500, price_paise=5000, units_per_carton=20, shelf_life_days=90, reorder_base=0)
    db.add_all([flour, oil, spice, pouch, carton, mur])
    db.flush()
    bom = Bom(product_id=mur.id, yield_packets=200)
    db.add(bom)
    for it, q in ((flour, 40000), (oil, 10000), (spice, 1200), (pouch, 200), (carton, 10)):
        bom.lines.append(BomLine(item_id=it.id, qty_base=q))
    db.add_all([
        Customer(name="Retailer A", kind="retailer", gstin="33BBBBB1111B1Z5", state="Tamil Nadu", phone="9000000001", address="Gandhi Market, Tiruchirappalli"),
        Customer(name="Distributor B", kind="distributor", gstin="29CCCCC2222C1Z5", state="Karnataka", phone="9000000002", address="Yeshwanthpur, Bengaluru"),
        Supplier(name="Cauvery Flour Mills", gstin="33DDDDD3333D1Z5", phone="9000000003"),
    ])
    db.flush()
    # Opening balances (paise value = qty x opening rate). FG opening cost of Rs 30/packet is an assumption.
    for item, qty, value in ((flour, 100000, 420000), (oil, 30000, 420000), (spice, 5000, 100000),
                             (pouch, 500, 125000), (carton, 30, 45000), (mur, 50, 150000)):
        stock.post(db, item, qty, value, "OPENING", doc_ref="Opening balance", entry_date=on, note="Section 5 master data")
    db.commit()


if __name__ == "__main__":
    if "--reset" in sys.argv:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed(s)
    print("Seeded. Logins: " + ", ".join(f"{u}/{p}" for u, _, _, p in USERS))
