"""BOM-driven production: issue raw material, complete the run, compare plan vs actual, cost it."""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select

from .. import config
from ..errors import BusinessError
from ..models import PACK, RAW, Bom, ProductionRun, RunLine, RunOverhead, now
from ..money import pct_var, round_div
from . import audit, stock


def get_bom(db, product_id: int) -> Bom:
    bom = db.scalar(select(Bom).where(Bom.product_id == product_id))
    if not bom:
        raise BusinessError("This product has no BOM (recipe) yet")
    return bom


def start_run(db, user, product_id: int, batches: int, issues: dict[int, int] | None = None,
              on: date | None = None, notes: str | None = None) -> ProductionRun:
    """Create a run and issue raw material. `issues` maps item_id -> actual base qty (default = BOM standard)."""
    if batches < 1:
        raise BusinessError("Batches must be at least 1")
    bom = get_bom(db, product_id)
    on = on or date.today()
    n = db.scalar(select(func.count(ProductionRun.id))) + 1
    product = bom.product
    expiry = on + timedelta(days=product.shelf_life_days) if product.shelf_life_days else None
    run = ProductionRun(run_no=f"RUN-{n:04d}", product_id=product_id, date=on, batches=batches,
                        expected_output=bom.yield_packets * batches, notes=notes, created_by=user.id,
                        batch_no=f"{product.code}-{on:%y%m%d}-{n:02d}", expiry=expiry)
    db.add(run)
    db.flush()
    issues = issues or {}
    for bl in bom.lines:
        if bl.item.kind != RAW:
            continue
        planned = bl.qty_base * batches
        actual = issues.get(bl.item_id, planned)
        value = stock.issue(db, bl.item, actual, "RUN_ISSUE", doc_id=run.id, doc_ref=run.run_no, user=user, entry_date=on)
        run.lines.append(RunLine(item_id=bl.item_id, phase="ISSUE", planned_base=planned, actual_base=actual, value_paise=value))
    audit.log(db, user, "run", run.id, "start", f"{run.run_no} {product.name} x{batches}")
    return run


def complete_run(db, user, run: ProductionRun, good: int, damaged: int = 0, pack_actuals: dict[int, int | None] | None = None):
    """Record actual output + wastage, consume packaging, add the finished goods to stock."""
    if run.status != "ISSUED":
        raise BusinessError("This run is already completed")
    if good <= 0 or damaged < 0:
        raise BusinessError("Good output must be above zero and damaged cannot be negative")
    bom = get_bom(db, run.product_id)
    pack_actuals = pack_actuals or {}
    for bl in bom.lines:
        if bl.item.kind != PACK:
            continue
        planned = bl.qty_base * run.batches
        actual = pack_actuals.get(bl.item_id)
        if actual is None:
            if bl.qty_base == bom.yield_packets:       # one-per-packet item (pouch): good + damaged
                actual = good + damaged
            else:                                       # e.g. cartons: scale the standard to what was made
                actual = round_div(planned * good, run.expected_output)
        value = stock.issue(db, bl.item, actual, "RUN_PACK", doc_id=run.id, doc_ref=run.run_no, user=user,
                            batch_no=run.batch_no, entry_date=run.date) if actual > 0 else 0
        run.lines.append(RunLine(item_id=bl.item_id, phase="PACK", planned_base=planned, actual_base=actual, value_paise=value))
    run.good_output, run.wastage_packets, run.status, run.completed_at = good, damaged, "COMPLETED", now()
    db.flush()
    total = material_cost(run) + overhead_cost(run)
    stock.receive(db, run.product, good, total, "RUN_OUTPUT", doc_id=run.id, doc_ref=run.run_no, user=user,
                  batch_no=run.batch_no, entry_date=run.date)
    audit.log(db, user, "run", run.id, "complete", f"{run.run_no} good={good} damaged={damaged}")


def add_overhead(db, user, run: ProductionRun, kind: str, amount_paise: int, note: str | None = None):
    if amount_paise <= 0:
        raise BusinessError("Overhead amount must be above zero")
    run.overheads.append(RunOverhead(kind=kind, amount_paise=amount_paise, note=note, created_by=user.id))
    if run.status == "COMPLETED":  # finished goods already in stock: raise their carrying value (qty unchanged)
        stock.post(db, run.product, 0, amount_paise, "RUN_OVERHEAD", doc_id=run.id, doc_ref=run.run_no, user=user,
                   note=f"{kind} added after completion", batch_no=run.batch_no)
    audit.log(db, user, "run", run.id, "overhead", f"{kind} {amount_paise}")


def material_cost(run) -> int:
    return sum(l.value_paise for l in run.lines)


def overhead_cost(run) -> int:
    return sum(o.amount_paise for o in run.overheads)


def summary(db, run: ProductionRun) -> dict:
    """Plan-vs-actual, variance flags and cost per packet for one run."""
    tol = config.VARIANCE_TOLERANCE_PCT
    lines = []
    for l in run.lines:
        v = pct_var(l.actual_base, l.planned_base)
        lines.append({"item": l.item, "phase": l.phase, "planned": l.planned_base, "actual": l.actual_base,
                      "var_pct": v, "flag": abs(v) > tol, "value": l.value_paise})
    out = {"run": run, "lines": lines, "material": material_cost(run), "overhead": overhead_cost(run),
           "expected": run.expected_output, "good": run.good_output, "yield_var": None, "yield_flag": False,
           "cost_per_packet": None, "margin": None, "margin_pct": None}
    out["total"] = out["material"] + out["overhead"]
    if run.status == "COMPLETED" and run.good_output:
        yv = pct_var(run.good_output, run.expected_output)
        out.update(yield_var=yv, yield_flag=abs(yv) > tol)
        # cost per good packet, to the paisa-hundredth; yield loss is absorbed by good packets
        cpp = (Decimal(out["total"]) / run.good_output).quantize(Decimal("0.01"), ROUND_HALF_UP) / 100
        out["cost_per_packet"] = cpp.quantize(Decimal("0.01"), ROUND_HALF_UP)
        price = Decimal(run.product.price_paise) / 100
        out["margin"] = (price - cpp).quantize(Decimal("0.01"), ROUND_HALF_UP)
        out["margin_pct"] = (out["margin"] * 100 / price).quantize(Decimal("0.1"), ROUND_HALF_UP) if price else None
    out["alerts"] = [f"{l['item'].name}: {l['var_pct']:+}%" for l in lines if l["flag"]] + \
                    ([f"Yield: {out['yield_var']:+}%"] if out["yield_flag"] else [])
    return out


def plan_from_orders(db) -> list[dict]:
    """Stretch: how many batches are needed to cover submitted/approved/invoiced-undispatched demand."""
    from ..models import Item, Order, OrderLine
    rows = db.execute(select(OrderLine.item_id, func.sum(OrderLine.qty)).join(Order)
                      .where(Order.status.in_(("SUBMITTED", "APPROVED", "INVOICED"))).group_by(OrderLine.item_id)).all()
    plan = []
    for item_id, demand in rows:
        item = db.get(Item, item_id)
        bom = db.scalar(select(Bom).where(Bom.product_id == item_id))
        have, _ = stock.balance(db, item_id)
        short = max(int(demand) - have, 0)
        if short and bom:
            plan.append({"item": item, "demand": int(demand), "stock": have, "short": short,
                         "batches": -(-short // bom.yield_packets)})
    return plan
