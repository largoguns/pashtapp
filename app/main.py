"""Aplicación FastAPI de PashtAPP."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal, engine, init_db
from app.deps import LoginRequired
from app.routers import api, auth, dashboard, export, loans, settings, transactions
from app.services.auth import ensure_admin
from app.services.seed import seed_defaults
from app.templating import templates

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("pashtapp")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with SessionLocal() as db:
        ensure_admin(db)
        seed_defaults(db)
    yield


def create_app() -> FastAPI:
    cfg = get_settings()
    app = FastAPI(
        title=cfg.app_name,
        description="Finanzas Domésticas Saeta",
        lifespan=lifespan,
        docs_url=None if cfg.is_production else "/docs",
        redoc_url=None,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, _exc: LoginRequired):
        if request.headers.get("HX-Request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.get("/manifest.json", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC_DIR / "manifest.json", media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        # Servido desde la raíz para que su ámbito cubra toda la app.
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/offline", include_in_schema=False)
    def offline(request: Request):
        return templates.TemplateResponse(request, "offline.html", {})

    @app.exception_handler(404)
    async def _not_found(request: Request, exc):
        if request.url.path.startswith("/api/") or request.headers.get("HX-Request") == "true":
            return JSONResponse({"detail": getattr(exc, "detail", "No encontrado")}, status_code=404)
        return templates.TemplateResponse(request, "404.html", {}, status_code=404)

    for r in (auth, dashboard, transactions, loans, settings, export, api):
        app.include_router(r.router)
    return app


app = create_app()
