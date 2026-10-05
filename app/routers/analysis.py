"""Pantallas de análisis: gasto por categoría, matriz anual e histórico de luz."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.models import Category
from app.services.analytics import category_matrix, donut_data, expenses_by_category, utility_series
from app.services.periods import available_periods, current_period, period_bounds
from app.templating import templates
from app.utils import shift_month
from app.views import resolve_month

router = APIRouter(prefix="/analysis", dependencies=[Depends(require_user)])


@router.get("")
def analysis_home():
    return RedirectResponse("/analysis/categories", status_code=303)


@router.get("/categories")
def by_category(request: Request, y: int | None = None, m: int | None = None, db: Session = Depends(get_db)):
    year, month = resolve_month(db, y, m)
    py, pm = shift_month(year, month, -1)
    current, previous = expenses_by_category(db, year, month), expenses_by_category(db, py, pm)
    cats = {c.id: c for c in db.scalars(select(Category)).all()}
    total = round(sum(v for v in current.values() if v > 0), 2)
    rows = []
    for cid in set(current) | set(previous):
        spent, prev = current.get(cid, 0.0), previous.get(cid, 0.0)
        if not spent and not prev:
            continue
        cat = cats.get(cid)
        limit = cat.monthly_budget_limit if cat else None
        rows.append({
            "category": cat, "name": cat.name if cat else "Sin categoría",
            "color": cat.color_hex if cat else "#94a3b8", "spent": spent, "prev": prev,
            "delta": round(spent - prev, 2), "share": round(100 * spent / total, 1) if total and spent > 0 else 0.0,
            "limit": limit, "budget_pct": round(100 * spent / limit, 1) if limit else None,
        })
    rows.sort(key=lambda r: -r["spent"])
    start, end = period_bounds(db, year, month)
    return templates.TemplateResponse(request, "analysis/categories.html", {
        "tab": "categories", "year": year, "month": month, "prev": (py, pm), "next": shift_month(year, month, 1),
        "is_current_month": current_period(db) == (year, month), "period_start": start, "period_end": end,
        "current": current_period(db), "periods": available_periods(db),
        "rows": rows, "total": total, "prev_total": round(sum(v for v in previous.values() if v > 0), 2),
        "donut": donut_data(db, year, month),
    })


@router.get("/matrix")
def matrix(request: Request, y: int | None = None, db: Session = Depends(get_db)):
    year = y if y and 1900 < y < 3000 else current_period(db)[0]
    cy, cm = current_period(db)
    return templates.TemplateResponse(request, "analysis/matrix.html", {
        "tab": "matrix", "matrix": category_matrix(db, year), "month": cm if cy == year else None,
        "years": list(available_periods(db)),
    })


@router.get("/utility")
def utility(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "analysis/utility.html", {
        "tab": "utility", "utility": utility_series(db),
    })
