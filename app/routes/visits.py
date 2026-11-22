from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..db import get_db
from ..errors import BusinessError
from ..models import Customer, Visit
from ..web import render, require

router = APIRouter()


@router.get("/visits")
def visits(request: Request, user=Depends(require("salesman", "admin")), db=Depends(get_db)):
    q = select(Visit).order_by(Visit.id.desc()).limit(100)
    if user.role == "salesman":
        q = q.where(Visit.salesman_id == user.id)
    customers = db.scalars(select(Customer).where(Customer.active.is_(True)).order_by(Customer.name)).all()
    return render(request, "visits.html", user, visits=db.scalars(q).all(), customers=customers)


@router.post("/api/visits")
async def check_in(request: Request, user=Depends(require("salesman")), db=Depends(get_db)):
    d = await request.json()
    if not db.get(Customer, int(d["customer_id"])):
        raise BusinessError("Pick a valid customer")
    lat, lon = d.get("lat"), d.get("lon")
    if lat is not None and not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
        raise BusinessError("That GPS position is not valid")
    v = Visit(salesman_id=user.id, customer_id=int(d["customer_id"]), lat=lat, lon=lon, accuracy_m=d.get("accuracy"), note=(d.get("note") or "")[:200] or None)
    db.add(v); db.commit()
    return JSONResponse({"ok": True, "id": v.id})
