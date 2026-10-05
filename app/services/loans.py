"""Amortización francesa y simulador de amortización anticipada (Mejora 3)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum

from sqlalchemy.orm import Session

from app.models import Loan, LoanInstallment
from app.utils import add_months


class Strategy(str, Enum):
    REDUCE_TERM = "REDUCE_TERM"
    REDUCE_FEE = "REDUCE_FEE"


def monthly_rate(annual_pct: float) -> float:
    return annual_pct / 100.0 / 12.0


def annuity(capital: float, annual_pct: float, months: int, residual: float = 0.0) -> float:
    """Cuota constante del sistema francés.

    Con ``residual`` (cuota final tipo *balloon*) la cuota amortiza ``capital - residual``
    en ``months`` pagos y el residual se liquida en un pago final adicional.
    """
    if months <= 0:
        return 0.0
    r = monthly_rate(annual_pct)
    if r == 0:
        return (capital - residual) / months
    return (capital - residual * (1 + r) ** -months) * r / (1 - (1 + r) ** -months)


@dataclass
class Row:
    number: int
    due_date: date
    payment: float
    capital: float
    interest: float
    remaining: float


def schedule_by_term(capital: float, annual_pct: float, months: int, first_due: date,
                     residual: float = 0.0) -> list[Row]:
    """Cuadro con plazo fijo: ``months`` cuotas (más el pago final si hay residual)."""
    residual = min(residual, capital)
    regular = months - 1 if residual > 0 else months
    fee = round(annuity(capital, annual_pct, regular, residual), 2)
    return _amortize(capital, annual_pct, fee, first_due, residual=residual, max_regular=regular)


def schedule_by_fee(capital: float, annual_pct: float, fee: float, first_due: date,
                    residual: float = 0.0) -> list[Row]:
    """Cuadro manteniendo la cuota: el plazo es lo que se ajusta."""
    r = monthly_rate(annual_pct)
    if capital > residual and fee <= capital * r + 1e-9:
        raise ValueError("La cuota no cubre los intereses: el préstamo no se amortizaría nunca.")
    return _amortize(capital, annual_pct, fee, first_due, residual=min(residual, capital))


def _amortize(capital, annual_pct, fee, first_due, *, residual=0.0, max_regular=None) -> list[Row]:
    r = monthly_rate(annual_pct)
    rows: list[Row] = []
    remaining = round(capital, 2)
    n = 0
    while remaining > 0.005 and n < 1200:
        n += 1
        interest = round(remaining * r, 2)
        principal = round(fee - interest, 2)
        if residual > 0:
            # Pago final (balloon): al agotar las cuotas regulares o si la siguiente bajaría del residual.
            last = (max_regular is not None and n > max_regular) or (
                max_regular is None and remaining - principal < residual - 0.01
            )
        else:
            last = principal >= remaining or (max_regular is not None and n >= max_regular)
        if last or principal >= remaining:
            principal = remaining
        payment = round(principal + interest, 2)
        remaining = round(remaining - principal, 2)
        rows.append(Row(n, add_months(first_due, n - 1), payment, principal, interest, max(remaining, 0.0)))
    return rows


def detect_residual(installments: list) -> float:
    """Capital de la cuota final si es un *balloon* (pago final muy superior a la cuota media)."""
    if len(installments) < 3:
        return 0.0
    payments = sorted(i.payment_amount for i in installments[:-1])
    median = payments[len(payments) // 2]
    last = installments[-1]
    return round(last.capital_amount, 2) if last.payment_amount > 1.5 * median else 0.0


def first_due_date(start_date: date) -> date:
    return add_months(start_date, 1)


def generate_installments(loan: Loan, paid_until: date | None = None) -> None:
    """(Re)genera el cuadro de amortización de un préstamo en memoria."""
    loan.installments.clear()
    rows = schedule_by_term(
        loan.initial_capital, loan.annual_interest_rate, loan.term_months, first_due_date(loan.start_date)
    )
    for row in rows:
        loan.installments.append(
            LoanInstallment(
                installment_number=row.number,
                due_date=row.due_date,
                payment_amount=row.payment,
                capital_amount=row.capital,
                interest_amount=row.interest,
                remaining_capital=row.remaining,
                is_paid=bool(paid_until and row.due_date <= paid_until),
            )
        )


@dataclass
class LoanStatus:
    outstanding: float
    paid_installments: int
    remaining_installments: int
    current_fee: float
    pending_interest: float
    paid_capital: float
    progress_pct: float
    next_due: date | None
    end_date: date | None


def loan_status(loan: Loan) -> LoanStatus:
    insts = list(loan.installments)
    unpaid = [i for i in insts if not i.is_paid]
    paid = [i for i in insts if i.is_paid]
    if unpaid:
        first = unpaid[0]
        outstanding = round(first.remaining_capital + first.capital_amount, 2)
    else:
        outstanding = 0.0 if insts else loan.initial_capital
    paid_capital = round(loan.initial_capital - outstanding, 2)
    return LoanStatus(
        outstanding=outstanding,
        paid_installments=len(paid),
        remaining_installments=len(unpaid),
        current_fee=unpaid[0].payment_amount if unpaid else loan.monthly_fee,
        pending_interest=round(sum(i.interest_amount for i in unpaid), 2),
        paid_capital=paid_capital,
        progress_pct=round(100 * paid_capital / loan.initial_capital, 1) if loan.initial_capital else 0.0,
        next_due=unpaid[0].due_date if unpaid else None,
        end_date=insts[-1].due_date if insts else None,
    )


@dataclass
class PrepaymentResult:
    loan_id: int
    loan_name: str
    strategy: str
    prepayment_amount: float
    outstanding_capital: float
    new_outstanding_capital: float
    current_fee: float
    current_remaining_months: int
    current_pending_interest: float
    current_end_date: date | None
    new_fee: float
    new_remaining_months: int
    new_pending_interest: float
    new_end_date: date | None
    interest_savings: float
    months_saved: int
    fee_reduction: float
    final_payment: float = 0.0       # Cuota final (balloon) del escenario actual, si existe
    new_final_payment: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("current_end_date", "new_end_date"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def simulate_prepayment(loan: Loan, prepayment_amount: float, strategy: Strategy | str) -> PrepaymentResult:
    strategy = Strategy(strategy)
    status = loan_status(loan)
    if prepayment_amount <= 0:
        raise ValueError("El importe a amortizar debe ser positivo.")
    if status.remaining_installments == 0 or status.outstanding <= 0:
        raise ValueError("El préstamo no tiene capital pendiente.")
    if prepayment_amount > status.outstanding + 0.005:
        raise ValueError(f"El importe supera el capital pendiente ({status.outstanding:.2f} €).")

    rate = loan.annual_interest_rate
    n = status.remaining_installments
    next_due = status.next_due or first_due_date(loan.start_date)
    residual = detect_residual(list(loan.installments))
    unpaid = [i for i in loan.installments if not i.is_paid]
    regular_fee = next((i.payment_amount for i in unpaid if not residual or i is not unpaid[-1]), loan.monthly_fee)

    # Escenario actual recalculado con el mismo modelo, para comparar en igualdad de condiciones.
    if residual and n == 1:
        baseline = schedule_by_term(status.outstanding, rate, 1, next_due)
    else:
        baseline = _amortize(status.outstanding, rate, regular_fee, next_due, residual=residual,
                             max_regular=n - 1 if residual else n)
    base_interest = round(sum(r.interest for r in baseline), 2)
    base_fee = baseline[0].payment if baseline else 0.0

    new_capital = round(status.outstanding - prepayment_amount, 2)
    if new_capital <= 0.005:
        rows: list[Row] = []
    elif strategy is Strategy.REDUCE_FEE:
        rows = schedule_by_term(new_capital, rate, n, next_due, residual=residual)
    else:
        rows = schedule_by_fee(new_capital, rate, base_fee, next_due, residual=residual)

    new_interest = round(sum(r.interest for r in rows), 2)
    new_fee = rows[0].payment if rows else 0.0
    return PrepaymentResult(
        loan_id=loan.id,
        loan_name=loan.name,
        strategy=strategy.value,
        prepayment_amount=round(prepayment_amount, 2),
        outstanding_capital=status.outstanding,
        new_outstanding_capital=max(new_capital, 0.0),
        current_fee=round(base_fee, 2),
        current_remaining_months=n,
        current_pending_interest=base_interest,
        current_end_date=baseline[-1].due_date if baseline else None,
        new_fee=round(new_fee, 2),
        new_remaining_months=len(rows),
        new_pending_interest=new_interest,
        new_end_date=rows[-1].due_date if rows else None,
        interest_savings=round(base_interest - new_interest, 2),
        months_saved=n - len(rows),
        fee_reduction=round(base_fee - new_fee, 2),
        final_payment=baseline[-1].payment if residual and baseline else 0.0,
        new_final_payment=rows[-1].payment if residual and rows else 0.0,
    )


def infer_rate(capital: float, months: int, fee: float) -> float:
    """Tipo nominal anual (%) que hace que ``annuity(capital, tipo, months) == fee``."""
    if fee * months <= capital + 1e-6:
        return 0.0
    lo, hi = 0.0, 100.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if annuity(capital, mid, months) > fee:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)


def create_loan(
    db: Session,
    *,
    name: str,
    initial_capital: float,
    annual_interest_rate: float,
    term_months: int,
    start_date: date,
    monthly_fee: float | None = None,
    mark_paid_until: date | None = None,
) -> Loan:
    fee = monthly_fee or round(annuity(initial_capital, annual_interest_rate, term_months), 2)
    loan = Loan(
        name=name,
        initial_capital=initial_capital,
        annual_interest_rate=annual_interest_rate,
        term_months=term_months,
        start_date=start_date,
        monthly_fee=fee,
        active=True,
    )
    generate_installments(loan, paid_until=mark_paid_until)
    db.add(loan)
    db.flush()
    return loan

