"""Construcción del contexto del tablero mensual, compartido por varias rutas."""
from __future__ import annotations

from datetime import date

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category
from app.services.analytics import budget_statuses
from app.services.balances import month_summary, month_transactions_query
from app.services.periods import available_periods, current_period, estimated_start, get_start_day, period_bounds
from app.templating import templates
from app.utils import shift_month, today


def _as_int(v: int | str | None) -> int | None:
    if isinstance(v, int):
        return v
    return int(v) if v and str(v).strip().isdigit() else None


def resolve_month(db: Session, y: int | str | None, m: int | str | None) -> tuple[int, int]:
    """Mes contable visible; valores ausentes o inválidos caen en el mes contable actual."""
    y, m = _as_int(y), _as_int(m)
    if y and 1900 < y < 3000 and m and 1 <= m <= 12:
        return y, m
    return current_period(db)


def categories(db: Session) -> list[Category]:
    return list(db.scalars(select(Category).order_by(Category.name)).all())


def month_context(db: Session, year: int, month: int) -> dict:
    txs = list(db.scalars(month_transactions_query(year, month)).unique().all())
    py, pm = shift_month(year, month, -1)
    ny, nm = shift_month(year, month, 1)
    t = today()
    current = current_period(db)
    start, end = period_bounds(db, year, month)
    ctx = {
        "year": year,
        "month": month,
        "prev": (py, pm),
        "next": (ny, nm),
        "period_start": start,
        "period_end": end,
        "is_current_month": current == (year, month),
        "current": current,
        "periods": available_periods(db),
        # Fecha por defecto del alta: hoy en el mes en curso; si no, el día 1 (o el inicio del periodo).
        "default_date": t if current == (year, month) else (date(year, month, 1) if start <= date(year, month, 1) <= end else start),
        "summary": month_summary(db, year, month),
        "incomes": [tx for tx in txs if tx.is_income],
        "fixed": [tx for tx in txs if tx.is_fixed and not tx.is_income],
        "variable": [tx for tx in txs if not tx.is_fixed and not tx.is_income],
        "transactions": txs,
        "budgets": budget_statuses(db, year, month),
        "categories": categories(db),
    }
    # ¿Ya toca cobrar el salario del mes siguiente? Aviso para «pasar de hoja», como en el Excel.
    start_day = get_start_day(db)
    ctx["next_salary_due"] = ctx["is_current_month"] and start_day > 1 and t >= estimated_start(ny, nm, start_day)
    return ctx


def render_month_fragments(request: Request, db: Session, year: int, month: int, toast: str | None = None):
    """Respuesta HTMX con todos los bloques del mes como OOB swaps."""
    ctx = month_context(db, year, month)
    ctx["toast"] = toast
    return templates.TemplateResponse(request, "partials/month_oob.html", ctx)
