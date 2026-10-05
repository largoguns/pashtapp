from datetime import date

import pytest

from app.models import Category, SavingsAccount, SavingsMovement, Transaction
from app.services.balances import month_summary
from app.services.savings import reconcile, simulate, summary, events
from app.services.transactions import create_movement


def _tx(db, name, amount, d, cat=None, settled=True, period=None, **kw):
    t = Transaction(date=d, settlement_date=d, period_year=(period or d)[0] if isinstance(period, tuple) else d.year,
                    period_month=period[1] if isinstance(period, tuple) else d.month, name=name, amount=amount,
                    is_income=amount > 0, is_settled=settled, category_id=cat.id if cat else None, **kw)
    db.add(t)
    db.flush()
    return t


def test_setup_links_category_history_and_counts_only_settled(client, db):
    ahorro = db.query(Category).filter_by(name="Otros").one()
    ahorro.name = "Ahorro"
    _tx(db, "Ahorro", -200, date(2026, 9, 1), ahorro)
    _tx(db, "Ahorro", -200, date(2026, 10, 1), ahorro)
    _tx(db, "Ahorro", -200, date(2026, 11, 1), ahorro, settled=False)  # previsto
    db.commit()
    r = client.post("/savings", data={"name": "Revolut", "annual_rate": "1,15", "opening_balance": "0",
                                      "opening_date": "2026-06-01", "category_id": str(ahorro.id),
                                      "link_history": "true"}, follow_redirects=False)
    assert r.status_code == 303 and "3+movimientos+vinculados" in r.headers["location"].replace("%20", "+")
    acc = db.query(SavingsAccount).one()
    s = summary(db, acc, ref=date(2026, 10, 5))
    assert s.total_deposits == 400 and len(s.pending) == 1
    assert 400 < s.balance < 401  # 400 € + unos céntimos de intereses estimados
    page = client.get("/savings").text
    assert "Revolut" in page and "Evolución y proyección" in page
    # Siguen siendo gastos de la cuenta principal (sep. y oct. arrastrados) y el ahorro no suma como disponible.
    assert month_summary(db, 2026, 10).current == -400


def test_new_movement_in_linked_category_is_linked(db):
    acc = SavingsAccount(name="Revolut", annual_rate=1.15, opening_balance=0, opening_date=date(2026, 1, 1))
    db.add(acc); db.flush()
    cat = Category(name="Ahorro", savings_account_id=acc.id)
    db.add(cat); db.flush()
    [tx] = create_movement(db, amount=150, name="Ahorro", category_id=cat.id, op_date=date(2026, 10, 2), is_settled=True)
    assert tx.savings_account_id == acc.id


def test_interest_estimation_compounds_daily_and_resets_on_reconcile(db):
    acc = SavingsAccount(name="R", annual_rate=1.15, opening_balance=10000, opening_date=date(2026, 1, 1))
    db.add(acc); db.flush()
    st = simulate(acc, events(db, acc), date(2027, 1, 1))
    # Un año al 1,15 % TIN con capitalización diaria ≈ 1,1566 % TAE.
    assert st[date(2027, 1, 1)].estimated == pytest.approx(10000 * ((1 + 0.0115 / 365) ** 365 - 1), abs=0.02)
    mv = reconcile(db, acc, 10050.00, date(2026, 6, 1))
    assert mv.amount == 50 and mv.kind == "interest"
    st = simulate(acc, events(db, acc), date(2026, 6, 2))
    assert st[date(2026, 6, 1)].book == 10050 and st[date(2026, 6, 1)].estimated == 0
    assert st[date(2026, 6, 2)].estimated == pytest.approx(10050 * 0.0115 / 365, abs=0.01)


def test_withdraw_to_main_account_and_direct_expense(client, db):
    acc = SavingsAccount(name="Revolut", annual_rate=0, opening_balance=1000, opening_date=date(2026, 1, 1))
    db.add(acc); db.commit()
    before = month_summary(db, *_current(db)).current
    client.post(f"/savings/{acc.id}/withdraw", data={"amount": "300", "on": date.today().isoformat()})
    client.post(f"/savings/{acc.id}/expense", data={"amount": "50", "name": "Taller", "on": "2026-03-10"})
    tx = db.query(Transaction).filter_by(savings_account_id=acc.id).one()
    assert tx.amount == 300 and tx.is_income and tx.is_settled and tx.category.name == "Traspasos"
    assert month_summary(db, *_current(db)).current == before + 300   # entra en la cuenta principal
    s = summary(db, acc)
    assert s.balance == 650 and s.total_withdrawals == 300 and s.total_expenses == 50
    assert db.query(SavingsMovement).filter_by(kind="expense").one().amount == -50


def test_withdrawal_does_not_start_accounting_month(db):
    from app.services.periods import salary_date, set_start_day

    set_start_day(db, 27)
    acc = SavingsAccount(name="R", annual_rate=0, opening_balance=0, opening_date=date(2026, 1, 1))
    db.add(acc); db.flush()
    _tx(db, "Traspaso desde R", 100, date(2026, 9, 20), period=(2026, 10), savings_account_id=acc.id)
    assert salary_date(db, 2026, 10) is None


def test_manual_link_toggle(client, db):
    acc = SavingsAccount(name="Revolut", annual_rate=0, opening_balance=0, opening_date=date(2026, 1, 1))
    db.add(acc); db.flush()
    tx = _tx(db, "Residencia", -160, date(2026, 6, 1))
    db.commit()
    assert "Residencia" in client.get(f"/savings/{acc.id}/candidates?q=resid").text
    r = client.post(f"/savings/{acc.id}/link/{tx.id}")
    assert "Vinculado" in r.text
    db.expire_all()
    assert summary(db, acc).total_deposits == 160
    client.post(f"/savings/{acc.id}/link/{tx.id}")
    db.expire_all()
    assert summary(db, acc).total_deposits == 0


def _current(db):
    from app.services.periods import current_period

    return current_period(db)
