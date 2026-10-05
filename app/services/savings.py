"""Cuentas de ahorro (p. ej. Revolut al 1,15 % TIN).

El saldo se construye con:
  * el saldo inicial de la cuenta;
  * los traspasos con la cuenta principal: movimientos conciliados de ``transactions`` con
    ``savings_account_id``. Un gasto en Caixabank es una aportación (+) y un ingreso, una
    retirada (−). Así el traspaso sigue siendo un gasto normal de la cuenta principal;
  * los movimientos propios de la cuenta (``savings_movements``): gastos pagados directamente
    con la cuenta de ahorro e intereses reales.

Intereses: se estiman a diario (TIN/365, capitalización diaria, como Revolut) sobre el saldo.
Al «cuadrar» con el saldo real, la diferencia con el saldo apuntado se guarda como intereses
reales y la estimación vuelve a empezar desde ese día.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, SavingsAccount, SavingsMovement, Transaction
from app.utils import MONTH_SHORT, add_months, today


@dataclass
class Event:
    date: date
    amount: float
    kind: str        # deposit | withdrawal | expense | interest | opening
    name: str
    tx_id: int | None = None
    movement_id: int | None = None
    pending: bool = False


def events(db: Session, account: SavingsAccount, *, include_pending: bool = False) -> list[Event]:
    out: list[Event] = []
    q = select(Transaction).where(Transaction.savings_account_id == account.id)
    if not include_pending:
        q = q.where(Transaction.is_settled.is_(True))
    for tx in db.scalars(q).unique():
        when = tx.booking_date
        out.append(Event(when, round(-tx.amount, 2), "withdrawal" if tx.amount > 0 else "deposit", tx.name,
                         tx_id=tx.id, pending=not tx.is_settled))
    for mv in db.scalars(select(SavingsMovement).where(SavingsMovement.account_id == account.id)):
        out.append(Event(mv.date, mv.amount, mv.kind, mv.name, movement_id=mv.id))
    out.sort(key=lambda e: (e.date, e.kind != "interest"))
    return out


@dataclass
class DayState:
    book: float          # saldo apuntado (sin estimación)
    estimated: float     # intereses estimados desde el último cuadre


def simulate(account: SavingsAccount, evs: list[Event], until: date) -> dict[date, DayState]:
    """Saldo día a día desde la apertura hasta ``until`` (incluido)."""
    daily = account.annual_rate / 100 / 365
    by_day: dict[date, list[Event]] = defaultdict(list)
    for e in evs:
        if not e.pending:
            by_day[max(e.date, account.opening_date)].append(e)
    book, est = account.opening_balance, 0.0
    states: dict[date, DayState] = {}
    d = account.opening_date
    while d <= until:
        for e in by_day.get(d, ()):
            book += e.amount
            if e.kind == "interest":
                est = 0.0  # el cuadre sustituye la estimación por el interés real
        states[d] = DayState(round(book, 2), round(est, 2))
        est += (book + est) * daily  # el interés del día se abona al final del día
        d += timedelta(days=1)
    return states


@dataclass
class MonthRow:
    year: int
    month: int
    deposits: float = 0.0
    withdrawals: float = 0.0
    expenses: float = 0.0
    interest: float = 0.0     # reales + variación de la estimación
    balance: float = 0.0

    @property
    def label(self) -> str:
        return f"{MONTH_SHORT[self.month - 1]} {self.year % 100:02d}"


@dataclass
class SavingsSummary:
    account: SavingsAccount
    balance: float                # apuntado + intereses estimados
    book: float
    estimated_interest: float
    last_reconciled: date | None
    total_deposits: float
    total_withdrawals: float
    total_expenses: float
    total_interest: float         # reales + estimados
    months: list[MonthRow]
    monthly_rate_contribution: float
    projection: list[tuple[str, float]]
    projection_interest: float
    pending: list[Event] = field(default_factory=list)
    recent: list[Event] = field(default_factory=list)


def summary(db: Session, account: SavingsAccount, *, ref: date | None = None, horizon: int = 12) -> SavingsSummary:
    ref = ref or today()
    evs = events(db, account, include_pending=True)
    real = [e for e in evs if not e.pending]
    states = simulate(account, real, ref)
    now = states.get(ref) or DayState(account.opening_balance, 0.0)

    # Serie mensual (mes natural: los saldos bancarios van por fechas).
    months: list[MonthRow] = []
    cursor = date(account.opening_date.year, account.opening_date.month, 1)
    prev_est = 0.0
    while cursor <= ref:
        end = min(add_months(cursor, 1, day=1) - timedelta(days=1), ref)
        row = MonthRow(cursor.year, cursor.month)
        for e in real:
            if cursor <= e.date <= end:
                if e.kind == "deposit":
                    row.deposits += e.amount
                elif e.kind == "withdrawal":
                    row.withdrawals -= e.amount
                elif e.kind == "expense":
                    row.expenses -= e.amount
                elif e.kind == "interest":
                    row.interest += e.amount
        st = states.get(end, now)
        row.interest += st.estimated - prev_est
        prev_est = st.estimated
        row.balance = round(st.book + st.estimated, 2)
        for attr in ("deposits", "withdrawals", "expenses", "interest"):
            setattr(row, attr, round(getattr(row, attr), 2))
        months.append(row)
        cursor = add_months(cursor, 1, day=1)

    # Ritmo de ahorro: media neta de los últimos 3 meses con movimiento.
    recent_rows = [r for r in months if r.deposits or r.withdrawals or r.expenses][-3:]
    rate = round(sum(r.deposits - r.withdrawals - r.expenses for r in recent_rows) / len(recent_rows), 2) \
        if recent_rows else 0.0
    rate = max(rate, 0.0)
    bal, proj, start_bal = now.book + now.estimated, [], now.book + now.estimated
    monthly = account.annual_rate / 100 / 12
    d = date(ref.year, ref.month, 1)
    for _ in range(horizon):
        d = add_months(d, 1, day=1)
        bal = bal * (1 + monthly) + rate
        proj.append((f"{MONTH_SHORT[d.month - 1]} {d.year % 100:02d}", round(bal, 2)))
    proj_interest = round(bal - start_bal - rate * horizon, 2)

    interest_moves = [e for e in real if e.kind == "interest"]
    return SavingsSummary(
        account=account,
        balance=round(now.book + now.estimated, 2),
        book=now.book,
        estimated_interest=now.estimated,
        last_reconciled=interest_moves[-1].date if interest_moves else None,
        total_deposits=round(sum(e.amount for e in real if e.kind == "deposit"), 2),
        total_withdrawals=round(-sum(e.amount for e in real if e.kind == "withdrawal"), 2),
        total_expenses=round(-sum(e.amount for e in real if e.kind == "expense"), 2),
        total_interest=round(sum(e.amount for e in interest_moves) + now.estimated, 2),
        months=months,
        monthly_rate_contribution=rate,
        projection=proj,
        projection_interest=proj_interest,
        pending=[e for e in evs if e.pending],
        recent=list(reversed(real))[:30],
    )


def book_balance(db: Session, account: SavingsAccount, on: date) -> float:
    real = [e for e in events(db, account) if e.date <= on]
    return round(account.opening_balance + sum(e.amount for e in real), 2)


def reconcile(db: Session, account: SavingsAccount, real_balance: float, on: date) -> SavingsMovement:
    """Registra como intereses reales la diferencia entre el saldo real y el apuntado."""
    diff = round(real_balance - book_balance(db, account, on), 2)
    mv = SavingsMovement(account_id=account.id, date=on, kind="interest", amount=diff,
                         name="Intereses (cuadre con saldo real)" if diff >= 0 else "Ajuste (cuadre con saldo real)",
                         notes=f"Saldo real {real_balance:.2f} €")
    db.add(mv)
    db.flush()
    return mv


def accounts(db: Session) -> list[SavingsAccount]:
    return list(db.scalars(select(SavingsAccount).where(SavingsAccount.active.is_(True)).order_by(SavingsAccount.id)))


def link_category_history(db: Session, category: Category, account: SavingsAccount) -> int:
    """Vincula a la cuenta los movimientos existentes de la categoría (aún sin vincular)."""
    txs = db.scalars(select(Transaction).where(
        Transaction.category_id == category.id, Transaction.savings_account_id.is_(None),
        Transaction.is_income.is_(False),
    )).unique().all()
    for tx in txs:
        tx.savings_account_id = account.id
    return len(txs)


def total_saved(db: Session) -> float | None:
    accs = accounts(db)
    if not accs:
        return None
    return round(sum(summary(db, a, horizon=0).balance for a in accs), 2)
