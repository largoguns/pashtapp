"""Alta de movimientos: inmediato, diferido y fraccionado (DEFINITION.md §4.2)."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, LoanInstallment, Transaction
from app.utils import add_months, today


class PaymentMode(str, Enum):
    NOW = "now"                  # Gasto inmediato
    DEFERRED_2D = "deferred_2d"  # Diferido 2 días
    NEXT_MONTH = "next_month"    # Diferido al mes siguiente
    SPLIT = "split"              # Fraccionado en N meses


DEFAULT_CATEGORY = "Otros"


def get_or_create_category(db: Session, name: str | None, **defaults) -> Category:
    name = (name or DEFAULT_CATEGORY).strip() or DEFAULT_CATEGORY
    cat = db.scalar(select(Category).where(func.lower(Category.name) == name.lower()))
    if cat is None:
        cat = Category(name=name, **defaults)
        db.add(cat)
        db.flush()
    return cat


def create_movement(
    db: Session,
    *,
    amount: float,
    name: str,
    category_id: int | None = None,
    op_date: date | None = None,
    mode: PaymentMode | str = PaymentMode.NOW,
    is_settled: bool = False,
    is_income: bool = False,
    is_fixed: bool = False,
    installments: int = 1,
    notes: str | None = None,
) -> list[Transaction]:
    """Crea uno o varios movimientos. ``amount`` se pasa en positivo; el signo lo da ``is_income``."""
    mode = PaymentMode(mode)
    op_date = op_date or today()
    total = abs(float(amount))
    sign = 1 if is_income else -1
    name = name.strip() or "Sin concepto"
    base = dict(name=name, is_income=is_income, category_id=category_id, is_fixed=is_fixed)

    if mode is PaymentMode.SPLIT and installments > 1:
        n = int(installments)
        group = str(uuid.uuid4())
        share = round(total / n, 2)
        txs = []
        for i in range(1, n + 1):
            # La última cuota absorbe el redondeo para que la suma sea exacta.
            part = share if i < n else round(total - share * (n - 1), 2)
            d = add_months(op_date, i)
            note = f"[Cuota {i}/{n}]" + (f" {notes}" if notes else "")
            txs.append(
                Transaction(
                    **base,
                    date=d,
                    settlement_date=d,
                    amount=sign * part,
                    is_settled=False,
                    installment_group_id=group,
                    installment_number=i,
                    installment_total=n,
                    notes=note,
                )
            )
        db.add_all(txs)
        db.flush()
        return txs

    if mode is PaymentMode.DEFERRED_2D:
        settlement, settled = op_date + timedelta(days=2), False
    elif mode is PaymentMode.NEXT_MONTH:
        settlement, settled = add_months(op_date, 1, day=1), False
    else:
        settlement, settled = op_date, bool(is_settled)

    tx = Transaction(
        **base, date=op_date, settlement_date=settlement, amount=sign * total, is_settled=settled, notes=notes
    )
    db.add(tx)
    db.flush()
    return [tx]


def set_settled(db: Session, tx: Transaction, value: bool) -> None:
    tx.is_settled = value
    if tx.loan_installment_id:
        inst = db.get(LoanInstallment, tx.loan_installment_id)
        if inst:
            inst.is_paid = value
