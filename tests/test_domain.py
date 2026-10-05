from datetime import date

import pytest

from app.models import Category, Transaction
from app.services.balances import initial_balance, month_summary
from app.services.loans import Strategy, create_loan, schedule_by_term, simulate_prepayment
from app.services.months import open_month
from app.services.parser import parse_bank_text
from app.services.settings_store import set_opening_balance
from app.services.transactions import PaymentMode, create_movement
from app.models import LoanInstallment, RecurringTemplate


def test_split_purchase_creates_linked_installments(db):
    txs = create_movement(db, amount=100, name="TV", op_date=date(2026, 1, 31), mode=PaymentMode.SPLIT, installments=3)
    assert [t.amount for t in txs] == [-33.33, -33.33, -33.34]
    assert len({t.installment_group_id for t in txs}) == 1
    assert [t.date for t in txs] == [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    assert txs[0].notes == "[Cuota 1/3]" and txs[2].installment_number == 3
    assert not any(t.is_settled for t in txs)


@pytest.mark.parametrize("mode,expected", [
    (PaymentMode.NOW, date(2026, 3, 30)),
    (PaymentMode.DEFERRED_2D, date(2026, 4, 1)),
    (PaymentMode.NEXT_MONTH, date(2026, 4, 1)),
])
def test_deferred_modes(db, mode, expected):
    [tx] = create_movement(db, amount=10, name="x", op_date=date(2026, 3, 30), mode=mode, is_settled=True)
    assert tx.settlement_date == expected
    assert tx.is_settled is (mode is PaymentMode.NOW)


def test_balances_and_carry_over(db):
    set_opening_balance(db, 1000, 2026, 1)
    create_movement(db, amount=2000, name="Nómina", op_date=date(2026, 1, 1), is_income=True, is_settled=True)
    create_movement(db, amount=500, name="Hipoteca", op_date=date(2026, 1, 2), is_fixed=True, is_settled=True)
    create_movement(db, amount=100, name="Super", op_date=date(2026, 1, 5), is_settled=False)
    # Diferido a febrero: no cuenta en enero.
    create_movement(db, amount=50, name="Tarjeta", op_date=date(2026, 1, 20), mode=PaymentMode.NEXT_MONTH)
    db.commit()
    jan = month_summary(db, 2026, 1)
    assert (jan.initial, jan.current, jan.projected) == (1000, 2500, 2400)
    assert jan.fixed_paid == 500 and jan.variable_pending == 100
    # Arrastre efectivo: sólo lo conciliado.
    assert initial_balance(db, 2026, 2) == 2500
    feb = month_summary(db, 2026, 2)
    assert feb.variable_pending == 50 and feb.projected == 2450
    # Meses anteriores al saldo configurado se calculan hacia atrás.
    assert initial_balance(db, 2025, 12) == 1000


def test_refund_nets_category_spend(db):
    from app.services.analytics import expenses_by_category

    cat = Category(name="Regalos", monthly_budget_limit=100)
    db.add(cat); db.flush()
    create_movement(db, amount=80, name="Regalo", category_id=cat.id, op_date=date(2026, 4, 2))
    db.add(Transaction(date=date(2026, 4, 3), name="Devolución", amount=30, category_id=cat.id))
    db.commit()
    assert expenses_by_category(db, 2026, 4)[cat.id] == 50


def test_budget_levels(db):
    from app.services.analytics import budget_statuses

    for name, spent in (("A", 50), ("B", 85), ("C", 120)):
        c = Category(name=name, monthly_budget_limit=100); db.add(c); db.flush()
        create_movement(db, amount=spent, name=name, category_id=c.id, op_date=date(2026, 5, 1))
    db.commit()
    levels = {b.category.name: b.level for b in budget_statuses(db, 2026, 5)}
    assert levels == {"A": "green", "B": "amber", "C": "red"}


def test_month_opening_is_idempotent_and_links_loans(db):
    db.add(RecurringTemplate(name="Internet", default_amount=40, day_of_month=31))
    loan = create_loan(db, name="Coche", initial_capital=12000, annual_interest_rate=6, term_months=48,
                       start_date=date(2026, 1, 15))
    db.commit()
    r1 = open_month(db, 2026, 2)
    assert (r1.created_fixed, r1.created_loan) == (1, 1)
    assert open_month(db, 2026, 2).skipped
    assert open_month(db, 2026, 2, force=True).total == 0
    txs = db.query(Transaction).all()
    assert {t.date for t in txs} == {date(2026, 2, 28), date(2026, 2, 15)}
    loan_tx = next(t for t in txs if t.loan_installment_id)
    assert loan_tx.amount == -loan.monthly_fee and loan_tx.is_fixed
    # Conciliar el cargo marca la cuota como pagada.
    from app.services.transactions import set_settled

    set_settled(db, loan_tx, True); db.commit()
    assert db.get(LoanInstallment, loan_tx.loan_installment_id).is_paid


def test_french_schedule_matches_bank_table():
    rows = schedule_by_term(18000, 11.2, 72, date(2023, 10, 11))
    assert (rows[0].payment, rows[0].capital, rows[0].interest, rows[0].remaining) == (344.46, 176.46, 168.0, 17823.54)
    assert rows[-1].remaining == 0 and len(rows) == 72


def test_balloon_schedule():
    rows = schedule_by_term(17272.78, 9.0, 49, date(2024, 8, 5), residual=9404.87)
    assert rows[47].remaining == 9404.87 and rows[0].payment == 266.33
    assert rows[-1].payment > 9000 and rows[-1].remaining == 0


def test_prepayment_strategies(db):
    loan = create_loan(db, name="Grueso", initial_capital=18000, annual_interest_rate=11.2, term_months=72,
                       start_date=date(2023, 9, 11), mark_paid_until=date(2026, 10, 5))
    db.commit()
    term = simulate_prepayment(loan, 3000, Strategy.REDUCE_TERM)
    fee = simulate_prepayment(loan, 3000, "REDUCE_FEE")
    assert term.interest_savings > fee.interest_savings > 0
    assert term.new_fee == term.current_fee and term.months_saved > 0 and term.new_end_date < term.current_end_date
    assert fee.new_remaining_months == fee.current_remaining_months and fee.new_fee < fee.current_fee
    full = simulate_prepayment(loan, term.outstanding_capital, Strategy.REDUCE_TERM)
    assert full.new_remaining_months == 0 and full.interest_savings == full.current_pending_interest
    with pytest.raises(ValueError):
        simulate_prepayment(loan, term.outstanding_capital + 1, Strategy.REDUCE_TERM)


@pytest.mark.parametrize("text,amount,category", [
    ("Pago con tarjeta de 42,30 EUR en REPSOL...", 42.30, "Gasolina"),
    ("Compra de 1.234,56€ en MERCADONA VALENCIA, tarjeta ****1234", 1234.56, "Comida"),
    ("Cargo de 9.99 EUR en LIDL", 9.99, "Comida"),
    ("Pago 5,00 EUR en kiosko", 5.0, "Otros"),
])
def test_sms_parser(text, amount, category):
    parsed = parse_bank_text(text)
    assert parsed.amount == amount and parsed.category == category
