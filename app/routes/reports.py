from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select

from .. import exports, reports as rp
from ..db import get_db
from ..errors import BusinessError
from ..models import Item
from ..web import get_user, render, require

router = APIRouter()
FLOOR_REPORTS = {"stock-summary", "stock-ledger", "production", "cost", "purchase-register"}


@router.get("/")
def home(request: Request, user=Depends(get_user), db=Depends(get_db)):
    if user.role == "salesman":
        from ..web import redirect
        return redirect("/orders/new")
    return render(request, "dashboard.html", user, d=rp.dashboard(db))


@router.get("/reports")
def index(request: Request, user=Depends(require("admin", "production", "accountant", "owner"))):
    keys = [(k, l) for k, (_, l) in rp.REPORTS.items() if user.role != "production" or k in FLOOR_REPORTS]
    return render(request, "reports.html", user, keys=keys)


@router.get("/reports/{key}")
def report(key: str, request: Request, fmt: str = "html", item_id: int = 0, user=Depends(require("admin", "production", "accountant", "owner")), db=Depends(get_db)):
    if key not in rp.REPORTS or (user.role == "production" and key not in FLOOR_REPORTS):
        raise BusinessError("Report not available")
    q = request.query_params
    d_to = date.fromisoformat(q.get("to") or date.today().isoformat())
    d_from = date.fromisoformat(q.get("from") or d_to.replace(day=1).isoformat())
    rep = rp.REPORTS[key][0](db, d_from, d_to, item_id=item_id or None)
    sub = f"Period: {d_from:%d-%m-%Y} to {d_to:%d-%m-%Y}"
    name = f"{key}-{d_from}-{d_to}"
    if fmt == "csv":
        return Response(exports.to_csv(rep), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if fmt == "xlsx":
        return Response(exports.to_xlsx(rep), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    if fmt == "pdf":
        return Response(exports.to_pdf(rep, sub), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})
    items = db.scalars(select(Item).order_by(Item.kind, Item.name)).all() if key == "stock-ledger" else []
    return render(request, "report.html", user, rep=rep, key=key, d_from=d_from, d_to=d_to, items=items, item_id=item_id, sub=sub)
