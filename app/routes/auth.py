from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import select

from ..db import get_db
from ..models import User
from ..security import verify_password
from ..services import audit
from ..web import flash, get_user, redirect, render

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not user.active or not verify_password(password, user.pw_hash):
        flash(request, "Wrong username or password", "err")
        return redirect("/login")
    request.session.clear()
    request.session["uid"] = user.id
    audit.log(db, user, "user", user.id, "login")
    db.commit()
    return redirect("/")


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")
