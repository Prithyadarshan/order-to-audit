from sqlalchemy import select, func

from app.benchmark import run_benchmark
from app.models import StockLedger
from app.seed import seed
from app.services import stock


def test_section5_benchmark(db):
    seed(db)
    rows = run_benchmark(db)
    failed = [r for r in rows if not r["ok"]]
    assert not failed, failed


def test_ledger_is_source_of_truth_and_traceable(db):
    seed(db)
    run_benchmark(db)
    # every movement after opening points at a source document
    orphans = db.scalar(select(func.count(StockLedger.id)).where(StockLedger.doc_type != "OPENING", StockLedger.doc_id.is_(None)))
    assert orphans == 0
    # opening + receipts - issues = closing, per item
    for item_id, (qty, _) in stock.balances(db).items():
        assert qty >= 0


def test_negative_stock_is_refused_and_rolls_back(db):
    import pytest
    from app.errors import StockError
    from app.models import Item
    seed(db)
    mur = db.scalar(select(Item).where(Item.code == "MUR200"))
    with pytest.raises(StockError):
        stock.issue(db, mur, 51, "DISPATCH")        # only 50 on hand
    assert stock.balance(db, mur.id)[0] == 50


def test_credit_note_returns_stock_and_keeps_invoice(db):
    from app.models import Customer, Item, User
    from app.services import sales
    seed(db)
    admin = db.scalar(select(User).where(User.username == "admin"))
    mur = db.scalar(select(Item).where(Item.code == "MUR200"))
    cust = db.scalar(select(Customer).where(Customer.name == "Retailer A"))
    o = sales.create_order(db, admin, cust.id, [(mur, 10)], client_ref="abc")
    assert sales.create_order(db, admin, cust.id, [(mur, 10)], client_ref="abc").id == o.id   # idempotent sync
    sales.approve(db, admin, o); inv = sales.create_invoice(db, admin, o); sales.dispatch(db, admin, inv)
    assert stock.balance(db, mur.id)[0] == 40
    cn = sales.cancel_invoice(db, admin, inv, "wrong rate")
    assert inv.status == "CANCELLED" and cn.amount_paise == inv.total_paise and stock.balance(db, mur.id)[0] == 50


def test_fefo_dispatch_takes_earliest_expiry_first(db):
    from datetime import date, timedelta
    from app.models import Customer, Item, StockLedger, User
    from app.services import production, sales
    seed(db)
    admin = db.scalar(select(User).where(User.username == "admin"))
    mur = db.scalar(select(Item).where(Item.code == "MUR200"))
    today = date.today()
    later = production.start_run(db, admin, mur.id, 1, on=today)                      # expires later
    production.complete_run(db, admin, later, good=100)
    earlier = production.start_run(db, admin, mur.id, 1, on=today - timedelta(days=10))  # made first, expires first
    production.complete_run(db, admin, earlier, good=100)
    cust = db.scalar(select(Customer).where(Customer.name == "Retailer A"))
    o = sales.create_order(db, admin, cust.id, [(mur, 180)])
    sales.approve(db, admin, o); inv = sales.create_invoice(db, admin, o); sales.dispatch(db, admin, inv)
    taken = {r.batch_no: -r.qty_base for r in db.scalars(select(StockLedger).where(StockLedger.doc_type == "DISPATCH"))}
    assert taken == {None: 50, earlier.batch_no: 100, later.batch_no: 30}
    assert stock.balance(db, mur.id)[0] == 70
    value = sum(-r.value_paise for r in db.scalars(select(StockLedger).where(StockLedger.doc_type == "DISPATCH")))
    assert value > 0
