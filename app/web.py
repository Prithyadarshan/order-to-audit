"""Shared web plumbing: templates, flash messages, login + role dependencies."""
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import BASE_DIR, FACTORY
from .db import get_db
from .models import User
from .money import fmt_qty, inr, paise_str

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals.update(inr=inr, fmt_qty=fmt_qty, paise=paise_str, FACTORY=FACTORY)

NAV = {
    "salesman": [("New order", "/orders/new"), ("My orders", "/orders"), ("My sales", "/my-sales")],
    "admin": [("Dashboard", "/"), ("Orders", "/orders"), ("Invoices", "/invoices"), ("Customers", "/customers"),
              ("Products", "/items"), ("Recipes", "/bom"), ("Stock", "/stock"), ("Reports", "/reports")],
    "production": [("Dashboard", "/"), ("Purchases", "/purchases"), ("Production", "/runs"), ("Recipes", "/bom"), ("Stock", "/stock"), ("Reports", "/reports")],
    "accountant": [("Dashboard", "/"), ("Invoices", "/invoices"), ("Stock", "/stock"), ("Reports", "/reports")],
    "owner": [("Dashboard", "/"), ("Invoices", "/invoices"), ("Stock", "/stock"), ("Reports", "/reports")],
}


class LoginRequired(Exception):
    pass


def get_user(request: Request, db=Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if not user or not user.active:
        raise LoginRequired()
    return user


def require(*roles):
    def dep(user: User = Depends(get_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Your role does not have access to this page")
        return user
    return dep


def render(request: Request, name: str, user: User | None = None, status_code: int = 200, **ctx):
    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(request, name, {"user": user, "nav": NAV.get(user.role, []) if user else [], "flash": flash,
                                                      "path": request.url.path, **ctx}, status_code=status_code)


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session["flash"] = [kind, msg]


def redirect(url: str):
    return RedirectResponse(url, status_code=303)
