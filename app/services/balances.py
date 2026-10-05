"""Cálculo de saldos mensuales (DEFINITION.md §4.1).

Cada movimiento cuenta en su mes contable (``period_year``/``period_month``), no en el de su
fecha: el mes empieza al cobrar el salario. La fecha de cargo sólo decide si ya está en la
cuenta (conciliado) o sigue pendiente.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services.settings_store import get_opening_balance

period_index = Transaction.period_year * 12 + (Transaction.period_month - 1)


def in_month(year: int, month: int):
    """Filtro del mes contable."""
    return and_(Transaction.period_year == year, Transaction.period_month == month)


def month_transactions_query(year: int, month: int) -> Select:
    return select(Transaction).where(in_month(year, month)).order_by(Transaction.date, Transaction.id)


def _settled_sum(db: Session, start_key: int, end_key: int) -> float:
    q = select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
        Transaction.is_settled.is_(True), period_index >= start_key, period_index < end_key
    )
    return float(db.scalar(q) or 0.0)


def initial_balance(db: Session, year: int, month: int) -> float:
    """Saldo de apertura: saldo configurado + arrastre efectivo de los meses previos."""
    opening = get_opening_balance(db)
    origin = opening.year * 12 + opening.month - 1
    target = year * 12 + month - 1
    if target >= origin:
        return round(opening.amount + _settled_sum(db, origin, target), 2) + 0.0
    return round(opening.amount - _settled_sum(db, target, origin), 2) + 0.0


@dataclass
class MonthSummary:
    year: int
    month: int
    initial: float
    income_planned: float      # Ingresos previstos (todos los del mes)
    income_settled: float      # Ingresos reales cobrados
    income_pending: float
    fixed_paid: float          # Importes en positivo
    fixed_pending: float
    variable_paid: float
    variable_pending: float
    current: float             # Saldo actual en cuenta (efectivo)
    projected: float           # Saldo proyectado a fin de mes

    @property
    def expenses_total(self) -> float:
        return self.fixed_paid + self.fixed_pending + self.variable_paid + self.variable_pending


def month_summary(db: Session, year: int, month: int) -> MonthSummary:
    def s(cond):
        return func.coalesce(func.sum(case((cond, Transaction.amount), else_=0.0)), 0.0)

    income = Transaction.is_income.is_(True)
    expense = Transaction.is_income.is_(False)  # incluye devoluciones (importe positivo) que netean
    settled = Transaction.is_settled.is_(True)
    pending = Transaction.is_settled.is_(False)
    fixed = Transaction.is_fixed.is_(True)
    variable = Transaction.is_fixed.is_(False)

    row = db.execute(
        select(
            s(and_(income, settled)),
            s(and_(income, pending)),
            s(and_(expense, fixed, settled)),
            s(and_(expense, fixed, pending)),
            s(and_(expense, variable, settled)),
            s(and_(expense, variable, pending)),
        ).where(in_month(year, month))
    ).one()
    # "+ 0.0" normaliza los -0.0 que produce SQLite al sumar importes negativos nulos.
    inc_settled, inc_pending, fx_paid, fx_pending, var_paid, var_pending = (float(v) + 0.0 for v in row)

    initial = initial_balance(db, year, month)
    current = initial + inc_settled + fx_paid + var_paid
    # Proyección: todo lo pendiente del mes (gastos y también ingresos aún no cobrados).
    projected = current + inc_pending + fx_pending + var_pending
    return MonthSummary(
        year=year,
        month=month,
        initial=initial,
        income_planned=round(inc_settled + inc_pending, 2),
        income_settled=round(inc_settled, 2),
        income_pending=round(inc_pending, 2),
        fixed_paid=round(-fx_paid, 2) + 0.0,
        fixed_pending=round(-fx_pending, 2) + 0.0,
        variable_paid=round(-var_paid, 2) + 0.0,
        variable_pending=round(-var_pending, 2) + 0.0,
        current=round(current, 2),
        projected=round(projected, 2),
    )
