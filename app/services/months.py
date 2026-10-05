"""Transición de mes / apertura automática (DEFINITION.md §4.3)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import Loan, LoanInstallment, MonthOpening, RecurringTemplate, Transaction
from app.services.periods import estimated_range, get_start_day, template_date
from app.services.transactions import get_or_create_category, savings_link_for

LOAN_CATEGORY = "Préstamos"


@dataclass
class OpeningReport:
    year: int
    month: int
    created_fixed: int = 0
    created_loan: int = 0
    reconciled: int = 0
    skipped: bool = False

    @property
    def total(self) -> int:
        return self.created_fixed + self.created_loan


def is_opened(db: Session, year: int, month: int) -> bool:
    return db.scalar(
        select(MonthOpening.id).where(MonthOpening.year == year, MonthOpening.month == month)
    ) is not None


def mark_opened(db: Session, year: int, month: int) -> None:
    if not is_opened(db, year, month):
        db.add(MonthOpening(year=year, month=month))


def open_month(db: Session, year: int, month: int, force: bool = False) -> OpeningReport:
    """Inserta los fijos recurrentes y las cuotas de préstamo del mes si aún no existen.

    Sólo se ejecuta automáticamente la primera vez que se visita un mes; con ``force``
    se vuelve a evaluar (útil tras crear nuevas plantillas) sin duplicar nada.
    """
    report = OpeningReport(year, month)
    if not force and is_opened(db, year, month):
        report.skipped = True
        return report

    start_day = get_start_day(db)
    start, end = estimated_range(year, month, start_day)
    in_period = (Transaction.period_year == year, Transaction.period_month == month)

    templates = db.scalars(select(RecurringTemplate).where(RecurringTemplate.active.is_(True))).all()
    for tpl in templates:
        exists = db.scalar(
            select(Transaction.id).where(
                Transaction.template_id == tpl.id, *in_period
            )
        )
        if exists:
            continue
        d = template_date(year, month, tpl.day_of_month or 1, start_day)
        amount = abs(tpl.default_amount)
        db.add(
            Transaction(
                period_year=year,
                period_month=month,
                date=d,
                settlement_date=d,
                name=tpl.name,
                amount=amount if tpl.is_income else -amount,
                is_income=tpl.is_income,
                category_id=tpl.category_id,
                is_fixed=not tpl.is_income,  # el salario es una previsión: su importe varía
                is_settled=False,
                template_id=tpl.id,
                savings_account_id=None if tpl.is_income else savings_link_for(db, tpl.category_id),
            )
        )
        report.created_fixed += 1

    installments = db.execute(
        select(LoanInstallment, Loan)
        .join(Loan, Loan.id == LoanInstallment.loan_id)
        .where(Loan.active.is_(True), and_(LoanInstallment.due_date >= start, LoanInstallment.due_date < end))
    ).all()
    loan_cat = None
    for inst, loan in installments:
        tx = db.scalar(select(Transaction).where(Transaction.loan_installment_id == inst.id))
        if tx is None:
            loan_cat = loan_cat or get_or_create_category(db, LOAN_CATEGORY, icon="bank", is_fixed_default=True)
            db.add(
                Transaction(
                    period_year=year,
                    period_month=month,
                    date=inst.due_date,
                    settlement_date=inst.due_date,
                    name=f"Préstamo {loan.name}",
                    amount=-abs(inst.payment_amount),
                    is_income=False,
                    category_id=loan_cat.id,
                    is_fixed=True,
                    is_settled=inst.is_paid,
                    loan_installment_id=inst.id,
                    notes=f"[Cuota {inst.installment_number}/{len(loan.installments)}]",
                )
            )
            report.created_loan += 1
        elif tx.is_settled != inst.is_paid:
            # Conciliación: una cuota marcada como pagada concilia el cargo y viceversa.
            if inst.is_paid:
                tx.is_settled = True
            else:
                inst.is_paid = tx.is_settled
            report.reconciled += 1

    mark_opened(db, year, month)
    db.commit()
    return report
