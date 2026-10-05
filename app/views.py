"""Construcción del contexto del tablero mensual, compartido por varias rutas."""
from __future__ import annotations

from datetime import date

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Loan
from app.services.analytics import budget_statuses, category_matrix, donut_data, utility_series
from app.services.balances import month_summary, month_transactions_query
from app.services.loans import loan_status
from app.templating import templates
from app.utils import shift_month, today


def _as_int(v: int | str | None) -> int | None:
    if isinstance(v, int):
        return v
    return int(v) if v and str(v).strip().isdigit() else None


def resolve_month(y: int | str | None, m: int | str | None) -> tuple[int, int]:
    """Año/mes visibles; valores ausentes o inválidos caen en el mes actual."""
    t = today()
    y, m = _as_int(y), _as_int(m)
    year = y if y and 1900 < y < 3000 else t.year
    month = m if m and 1 <= m <= 12 else t.month
    return year, month


def categories(db: Session) -> list[Category]:
    return list(db.scalars(select(Category).order_by(Category.name)).all())


def month_context(db: Session, year: int, month: int, *, full: bool = True) -> dict:
    txs = list(db.scalars(month_transactions_query(year, month)).unique().all())
    py, pm = shift_month(year, month, -1)
    ny, nm = shift_month(year, month, 1)
    t = today()
    ctx = {
        "year": year,
        "month": month,
        "prev": (py, pm),
        "next": (ny, nm),
        "is_current_month": (t.year, t.month) == (year, month),
        "default_date": t if (t.year, t.month) == (year, month) else date(year, month, 1),
        "summary": month_summary(db, year, month),
        "incomes": [tx for tx in txs if tx.is_income],
        "fixed": [tx for tx in txs if tx.is_fixed and not tx.is_income],
        "variable": [tx for tx in txs if not tx.is_fixed and not tx.is_income],
        "transactions": txs,
        "budgets": budget_statuses(db, year, month),
        "categories": categories(db),
        "donut": donut_data(db, year, month),
    }
    if full:
        loans = db.scalars(select(Loan).where(Loan.active.is_(True)).order_by(Loan.name)).unique().all()
        ctx["loans"] = [(loan, loan_status(loan)) for loan in loans]
        ctx["matrix"] = category_matrix(db, year)
        ctx["utility"] = utility_series(db)
    return ctx


def render_month_fragments(request: Request, db: Session, year: int, month: int, toast: str | None = None):
    """Respuesta HTMX con todos los bloques del mes como OOB swaps."""
    ctx = month_context(db, year, month, full=False)
    ctx["toast"] = toast
    return templates.TemplateResponse(request, "partials/month_oob.html", ctx)
