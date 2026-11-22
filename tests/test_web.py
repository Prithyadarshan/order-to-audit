"""The Section 5 scenario driven through the real HTTP forms, one role per step."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Customer, Invoice, Item, Order
from app.seed import seed
from app.services import stock


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed(s)
    with TestClient(app) as c:
        yield c


def login(c, u, p):
    c.cookies.clear()
    assert c.post("/login", data={"username": u, "password": p}, follow_redirects=False).status_code == 303


def bal(code):
    with SessionLocal() as s:
        it = s.scalar(select(Item).where(Item.code == code))
        return stock.balance(s, it.id)[0]


def test_full_scenario_over_http(client):
    c = client
    with SessionLocal() as s:
        ids = {i.code: i.id for i in s.scalars(select(Item))}
        ra, db_ = [x.id for x in s.scalars(select(Customer).order_by(Customer.id))]
    login(c, "production", "prod123")
    c.post("/purchases", data={"supplier_id": 1, "item_1": ids["FLOUR"], "qty_1": "60", "unit_1": "kg", "rate_1": "45"}, follow_redirects=False)
    r = c.post("/runs", data={"product_id": ids["MUR200"], "batches": 1, f"issue_{ids['OIL']}": "10.5", f"unit_{ids['OIL']}": "L"}, follow_redirects=False)
    rid = int(r.headers["location"].split("/")[-1])
    c.post(f"/runs/{rid}/complete", data={"good": 188, "damaged": 6, f"pack_{ids['CARTON20']}": 9}, follow_redirects=False)
    assert (bal("FLOUR"), bal("OIL"), bal("POUCH200"), bal("MUR200")) == (120000, 19500, 306, 238)
    login(c, "salesman_a", "sales123")
    r = c.post("/api/orders", json={"client_ref": "x1", "customer_id": ra, "lines": [{"item_id": ids["MUR200"], "qty": 150, "unit": "packet"}]})
    assert r.json()["ok"]
    assert c.post("/api/orders", json={"client_ref": "x1", "customer_id": ra, "lines": [{"item_id": ids["MUR200"], "qty": 150, "unit": "packet"}]}).json()["id"] == r.json()["id"]
    login(c, "salesman_b", "sales123")
    c.post("/api/orders", json={"customer_id": db_, "lines": [{"item_id": ids["MUR200"], "qty": 120, "unit": "packet"}]})
    login(c, "admin", "admin123")
    c.post("/orders/1/approve"); c.post("/orders/1/invoice")
    c.post("/invoices/1/dispatch"); c.post("/invoices/1/payment", data={"amount": "4000", "mode": "upi"})
    assert bal("MUR200") == 88
    page = c.get("/orders/2").text
    assert "short by <b>32</b>" in page and "IGST" in page
    c.post("/orders/2/approve", headers={"referer": "/orders/2"})
    with SessionLocal() as s:
        assert s.get(Order, 2).status == "SUBMITTED"      # approval refused
        inv = s.get(Invoice, 1)
        assert inv.total_paise == 787500 and sum(p.amount_paise for p in inv.payments) == 400000
    login(c, "production", "prod123")
    c.post(f"/runs/{rid}/overhead", data={"kind": "labour", "amount": "1200"}); c.post(f"/runs/{rid}/overhead", data={"kind": "gas/power", "amount": "600"})
    assert "₹31.14" in c.get(f"/runs/{rid}").text
    login(c, "accountant", "acct123")
    for k in ("sales-register", "gst-summary", "stock-summary", "stock-ledger", "production", "outstanding", "purchase-register"):
        for fmt in ("html", "csv", "xlsx", "pdf"):
            assert c.get(f"/reports/{k}?fmt={fmt}").status_code == 200
    assert "3875.00" in c.get("/reports/outstanding?fmt=csv").text
    assert c.get("/invoices/1/pdf").content[:4] == b"%PDF"


def test_roles_are_enforced(client):
    login(client, "salesman_a", "sales123")
    assert client.get("/invoices").status_code == 403 and client.get("/runs").status_code == 403 and client.get("/reports").status_code == 403
    assert client.post("/orders/1/approve").status_code == 403
    login(client, "accountant", "acct123")
    assert client.post("/invoices/1/dispatch").status_code == 403 and client.get("/purchases").status_code == 403
    client.cookies.clear()
    assert client.get("/stock", follow_redirects=False).headers["location"] == "/login"


def test_tamil_interface_for_floor_staff(client):
    login(client, "production", "prod123")
    assert "உற்பத்தி" not in client.get("/runs").text
    client.get("/lang/ta", headers={"referer": "/runs"})
    page = client.get("/runs").text
    assert "உற்பத்தி பதிவுகள்" in page and "கொள்முதல்" in page and "இருப்பு" in page
    assert "நல்ல பாக்கெட்டுகள்" in client.get("/runs/new").text or "செய்முறையை ஏற்று" in client.get("/runs/new").text
    client.get("/lang/en"); assert "Production runs" in client.get("/runs").text
