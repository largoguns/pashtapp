from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.services.months import open_month
from app.templating import templates
from app.views import month_context, render_month_fragments, resolve_month

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/")
def dashboard(request: Request, y: int | None = None, m: int | None = None, db: Session = Depends(get_db)):
    year, month = resolve_month(db, y, m)
    report = open_month(db, year, month)  # Apertura automática al visitar el mes (§4.3)
    ctx = month_context(db, year, month)
    ctx["opening_report"] = None if report.skipped else report
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@router.post("/months/{year}/{month}/open")
def reopen_month(request: Request, year: int, month: int, db: Session = Depends(get_db)):
    report = open_month(db, year, month, force=True)
    msg = f"Apertura: {report.created_fixed} fijos y {report.created_loan} cuotas añadidos"
    if request.headers.get("HX-Request") == "true":
        return render_month_fragments(request, db, year, month, toast=msg)
    return RedirectResponse(f"/?y={year}&m={month}", status_code=303)
