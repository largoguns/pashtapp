"""Meses contables (periodos).

Cada movimiento pertenece explícitamente a un mes contable (``period_year``/``period_month``),
igual que en el Excel se apuntaba en la hoja abierta. El mes contable «Octubre» empieza el
día en que se cobra el salario de finales de septiembre y dura hasta el siguiente cobro.

Reglas para *proponer* el mes de un movimiento nuevo a partir de su fecha:
  * Si el mes M ya tiene un ingreso cobrado, empieza en la fecha del primero de ellos.
  * Si no, empieza el «día estimado de cobro» (ajuste ``period_start_day``) del mes anterior;
    pero si esa fecha ya ha pasado y aún no se ha cobrado, el mes nuevo no ha empezado.
  * Con ``period_start_day = 1`` los meses contables coinciden con los naturales.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Transaction
from app.services.settings_store import get_setting, set_setting
from app.utils import clamp_day, shift_month, today

# Margen para considerar que un cobro «se está retrasando» (más allá, se usa la estimación).
_LATE_SALARY_WINDOW = timedelta(days=10)


def get_start_day(db: Session) -> int:
    try:
        return max(1, min(28, int(get_setting(db, "period_start_day", "1") or 1)))
    except ValueError:
        return 1


def set_start_day(db: Session, day: int) -> None:
    set_setting(db, "period_start_day", str(max(1, min(28, int(day)))))


def estimated_start(year: int, month: int, start_day: int) -> date:
    """Inicio estimado del mes contable: el día de cobro del mes natural anterior."""
    if start_day <= 1:
        return date(year, month, 1)
    py, pm = shift_month(year, month, -1)
    return clamp_day(py, pm, start_day)


def salary_date(db: Session, year: int, month: int) -> date | None:
    """Fecha del primer ingreso cobrado del mes contable (normalmente, el salario)."""
    return db.scalar(
        select(func.min(Transaction.date)).where(
            Transaction.period_year == year, Transaction.period_month == month,
            Transaction.is_income.is_(True), Transaction.is_settled.is_(True),
        )
    )


def period_start(db: Session, year: int, month: int, *, start_day: int | None = None,
                 ref: date | None = None) -> date:
    start_day = get_start_day(db) if start_day is None else start_day
    est = estimated_start(year, month, start_day)
    if start_day <= 1:
        return est
    paid = salary_date(db, year, month)
    if paid:
        return paid
    ref = ref or today()
    if est <= ref < est + _LATE_SALARY_WINDOW:
        return ref + timedelta(days=1)  # el salario se retrasa: el mes aún no ha empezado
    return est


def period_of(db: Session, d: date, *, ref: date | None = None) -> tuple[int, int]:
    """Mes contable que corresponde a la fecha ``d``."""
    start_day = get_start_day(db)
    if start_day <= 1:
        return d.year, d.month
    ny, nm = shift_month(d.year, d.month, 1)
    if d >= period_start(db, ny, nm, start_day=start_day, ref=ref):
        return ny, nm
    return d.year, d.month


def current_period(db: Session) -> tuple[int, int]:
    return period_of(db, today())


def period_bounds(db: Session, year: int, month: int) -> tuple[date, date]:
    """Primer y último día del mes contable (el último es estimado si aún no se ha cobrado)."""
    ny, nm = shift_month(year, month, 1)
    return period_start(db, year, month), period_start(db, ny, nm) - timedelta(days=1)


def estimated_range(year: int, month: int, start_day: int) -> tuple[date, date]:
    """Rango [inicio, fin) estimado, estable (no depende de cobros); para cuotas y plantillas."""
    ny, nm = shift_month(year, month, 1)
    return estimated_start(year, month, start_day), estimated_start(ny, nm, start_day)


def template_date(year: int, month: int, day: int, start_day: int) -> date:
    """Fecha de un fijo recurrente del día ``day`` dentro del mes contable."""
    if start_day > 1 and day >= start_day:
        py, pm = shift_month(year, month, -1)
        return clamp_day(py, pm, day)
    return clamp_day(year, month, day)


def period_key(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def available_periods(db: Session) -> dict[int, list[int]]:
    """Meses contables con movimientos, por año (incluye siempre el mes en curso)."""
    rows = db.execute(select(Transaction.period_year, Transaction.period_month).distinct()).all()
    out: dict[int, set[int]] = {}
    for y, m in rows:
        out.setdefault(y, set()).add(m)
    cy, cm = current_period(db)
    out.setdefault(cy, set()).add(cm)
    return {y: sorted(ms) for y, ms in sorted(out.items())}
