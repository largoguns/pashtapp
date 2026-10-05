"""Presupuestos por categoría (Mejora 4) y datos para la sección analítica."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.models import Category, Transaction, UtilityReading
from app.services.balances import booking_date, in_month
from app.utils import MONTH_SHORT

DONUT_MAX = 8


@dataclass
class BudgetStatus:
    category: Category
    spent: float
    limit: float
    pct: float

    @property
    def level(self) -> str:
        if self.pct > 100:
            return "red"
        if self.pct >= 80:
            return "amber"
        return "green"

    @property
    def bar_pct(self) -> float:
        return min(self.pct, 100.0)


def expenses_by_category(db: Session, year: int, month: int) -> dict[int | None, float]:
    rows = db.execute(
        select(Transaction.category_id, func.sum(Transaction.amount))
        .where(in_month(year, month), Transaction.is_income.is_(False))
        .group_by(Transaction.category_id)
    ).all()
    # Gasto neto: las devoluciones (importe positivo no-ingreso) restan de su categoría.
    return {cid: round(-float(total), 2) for cid, total in rows}


def budget_statuses(db: Session, year: int, month: int) -> list[BudgetStatus]:
    spent = expenses_by_category(db, year, month)
    cats = db.scalars(
        select(Category).where(Category.monthly_budget_limit.is_not(None), Category.monthly_budget_limit > 0)
        .order_by(Category.name)
    ).all()
    out = []
    for cat in cats:
        s = spent.get(cat.id, 0.0)
        out.append(BudgetStatus(cat, s, cat.monthly_budget_limit, round(100 * s / cat.monthly_budget_limit, 1)))
    return sorted(out, key=lambda b: -b.pct)


def donut_data(db: Session, year: int, month: int) -> dict:
    spent = expenses_by_category(db, year, month)
    cats = {c.id: c for c in db.scalars(select(Category)).all()}
    items = sorted(((k, v) for k, v in spent.items() if v > 0), key=lambda kv: -kv[1])
    labels = [cats[cid].name if cid in cats else "Sin categoría" for cid, _ in items]
    values = [v for _, v in items]
    colors = [cats[cid].color_hex if cid in cats else "#94a3b8" for cid, _ in items]
    # Más de DONUT_MAX porciones no se distinguen: el resto se agrupa en «Resto» (gris neutro).
    if len(items) > DONUT_MAX:
        rest = round(sum(values[DONUT_MAX - 1:]), 2)
        labels, values, colors = labels[:DONUT_MAX - 1] + ["Resto"], values[:DONUT_MAX - 1] + [rest], \
            colors[:DONUT_MAX - 1] + ["#475569"]
    return {"labels": labels, "values": values, "colors": colors}


def category_matrix(db: Session, year: int) -> dict:
    """Matriz categorías × meses del año (gasto en positivo)."""
    y = extract("year", booking_date)
    m = extract("month", booking_date)
    rows = db.execute(
        select(Transaction.category_id, m, func.sum(Transaction.amount))
        .where(y == year, Transaction.is_income.is_(False))
        .group_by(Transaction.category_id, m)
    ).all()
    cats = {c.id: c for c in db.scalars(select(Category)).all()}
    data: dict[int | None, list[float]] = {}
    for cid, month, total in rows:
        data.setdefault(cid, [0.0] * 12)[int(month) - 1] = round(-float(total), 2)
    lines = []
    for cid, values in data.items():
        cat = cats.get(cid)
        lines.append({
            "name": cat.name if cat else "Sin categoría",
            "color": cat.color_hex if cat else "#94a3b8",
            "values": values,
            "total": round(sum(values), 2),
            "avg": round(sum(values) / max(1, sum(1 for v in values if v)), 2),
        })
    lines.sort(key=lambda r: -r["total"])
    month_totals = [round(sum(r["values"][i] for r in lines), 2) for i in range(12)]
    peak = max((v for r in lines for v in r["values"]), default=0.0)
    lines = [r for r in lines if any(r["values"])]
    return {"year": year, "months": MONTH_SHORT, "rows": lines, "month_totals": month_totals,
            "total": round(sum(month_totals), 2), "peak": peak}


def utility_series(db: Session) -> dict:
    readings = db.scalars(select(UtilityReading).order_by(UtilityReading.year, UtilityReading.month)).all()
    years = sorted({r.year for r in readings})
    by_year = {y: {"amount": [None] * 12, "kwh": [None] * 12} for y in years}
    for r in readings:
        by_year[r.year]["amount"][r.month - 1] = r.amount
        by_year[r.year]["kwh"][r.month - 1] = r.kwh
    return {"months": MONTH_SHORT, "years": years, "by_year": by_year, "count": len(readings)}
