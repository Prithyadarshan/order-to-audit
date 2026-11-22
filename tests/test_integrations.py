import xml.etree.ElementTree as ET
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.benchmark import run_benchmark
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Invoice, Payment
from app.seed import seed


def _setup():
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with SessionLocal() as s:
        seed(s); run_benchmark(s)


def _login(c, u, p):
    c.cookies.clear(); c.post("/login", data={"username": u, "password": p}, follow_redirects=False)


def test_tally_xml_is_well_formed_and_balanced():
    _setup()
    with TestClient(app) as c:
        _login(c, "accountant", "acct123")
        r = c.get("/exports/tally.xml"); assert r.status_code == 200
        root = ET.fromstring(r.content)
        vouchers = root.findall(".//VOUCHER")
        assert [v.get("VCHTYPE") for v in vouchers] == ["Sales", "Receipt"]
        for v in vouchers:   # every voucher must balance to zero (debits negative, credits positive)
            assert sum(Decimal(a.text) for a in v.iter("AMOUNT")) == 0
        assert vouchers[0].find("VOUCHERNUMBER").text.startswith("INV/")
        assert [a.text for a in vouchers[0].iter("AMOUNT")] == ["-7875.00", "7500.00", "187.50", "187.50"]


def test_einvoice_and_ewaybill_mock_payloads():
    _setup()
    with TestClient(app) as c:
        _login(c, "admin", "admin123")
        j = c.get("/invoices/1/einvoice.json").json()
        assert j["ValDtls"]["TotInvVal"] == 7875.0 and j["ValDtls"]["CgstVal"] == 187.5 and len(j["Irn"]) == 64 and "MOCK" in j["_note"]
        assert j["BuyerDtls"]["Stcd"] == "33" and j["ItemList"][0]["HsnCd"] == "1905"
        w = c.get("/invoices/1/ewaybill.json?vehicle=TN45AB1234&km=12").json()
        assert w["vehicleNo"] == "TN45AB1234" and w["totInvValue"] == 7875.0 and w["toStateCode"] == 33


def test_visit_checkin_and_fefo_listing():
    _setup()
    with TestClient(app) as c:
        _login(c, "salesman_a", "sales123")
        assert c.post("/api/visits", json={"customer_id": 1, "lat": 10.8, "lon": 78.7, "accuracy": 12, "note": "stock check"}).json()["ok"]
        assert c.post("/api/visits", json={"customer_id": 1, "lat": 99, "lon": 78.7}).status_code == 400
        assert "stock check" in c.get("/visits").text
        _login(c, "admin", "admin123")
        assert "salesman_a".replace("_", " ").title() in c.get("/visits").text or "Salesman A" in c.get("/visits").text
        assert "Opening stock (no batch)" in c.get("/invoices/1").text
