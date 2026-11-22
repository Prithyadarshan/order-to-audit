import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, SECRET_KEY
from .db import Base, SessionLocal, engine
from .errors import BusinessError
from .routes import auth, masters, production, reports, sales, visits
from .seed import seed
from .web import LoginRequired, flash, render

@asynccontextmanager
async def lifespan(_):
    Base.metadata.create_all(engine)
    if os.getenv("SEED_ON_START", "1") == "1":
        with SessionLocal() as db:
            seed(db)  # no-op when users already exist
    yield


app = FastAPI(title="Order-to-Audit", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", max_age=int(timedelta(hours=12).total_seconds()))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
for r in (auth, sales, production, masters, reports, visits):
    app.include_router(r.router)




@app.get("/sw.js", include_in_schema=False)
def service_worker():  # served from the root so it can control /orders/new
    return FileResponse(BASE_DIR / "static" / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.get("/healthz", include_in_schema=False)
def health():
    return {"ok": True}


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/")


@app.exception_handler(LoginRequired)
async def _login(request: Request, exc):
    if _is_api(request):
        return JSONResponse({"ok": False, "error": "Please sign in again"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(BusinessError)
@app.exception_handler(ValueError)
async def _business(request: Request, exc):
    if _is_api(request):
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    flash(request, str(exc), "err")
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


@app.exception_handler(HTTPException)
async def _http(request: Request, exc: HTTPException):
    if _is_api(request):
        return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    return render(request, "error.html", None, status_code=exc.status_code, code=exc.status_code, message=exc.detail)
