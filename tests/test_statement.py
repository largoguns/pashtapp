"""Importación del extracto CSV de la cuenta de ahorro (formato Revolut)."""
from datetime import date

from app.models import Category, SavingsAccount, SavingsStatementLine, Transaction
from app.services.savings import summary
from app.services.statement_import import parse_statement

CSV = '''Fecha de transacción/valor,Descripción,TAE,TIN,Dinero entrante,Dinero saliente,Saldo
5 sept 2026,"Depósito en ""Cuenta Remunerada""",,,"1.500,00€",,"1.500,00€"
5 sept 2026,"Depósito en ""Cuenta Remunerada""",,,"200,00€",,"1.700,00€"
6 sept 2026,Interés neto pagado a Cuenta Remunerada del 6 sept 2026,1.15%,1.15%,"0,05€",,"1.700,05€"
17 sept 2026,"Retirada de ""Cuenta Remunerada""",,,,"50,00€","1.650,05€"
19 sept 2026,"Retirada de ""Cuenta Remunerada""",,,,"200,00€","1.450,05€"
1 Oct 2026,Interés neto pagado a Cuenta Remunerada del 1 oct 2026,1.15%,1.15%,"0,03€",,"1.450,08€"
'''


def _setup(db):
    acc = SavingsAccount(name="Revolut", annual_rate=1.15, opening_balance=0, opening_date=date(2026, 3, 1))
    db.add(acc); db.flush()
    cat = Category(name="Ahorro", savings_account_id=acc.id)
    db.add(cat); db.flush()
    ahorro = Transaction(date=date(2026, 9, 1), period_year=2026, period_month=9, name="Ahorro", amount=-200,
                         is_settled=True, category_id=cat.id, savings_account_id=acc.id)
    residencia = Transaction(date=date(2026, 6, 1), period_year=2026, period_month=6, name="Residencia",
                             amount=-160, is_settled=True, category_id=cat.id, savings_account_id=acc.id)
    db.add_all([ahorro, residencia]); db.commit()
    return acc, ahorro


def _upload(client, acc, text):
    return client.post(f"/savings/{acc.id}/statement",
                       files={"file": ("extracto.csv", text.encode("utf-8"), "text/csv")}, follow_redirects=False)


def _loc(r):
    from urllib.parse import unquote_plus

    return unquote_plus(r.headers["location"])


def test_parse_handles_spanish_dates_amounts_and_mojibake():
    good = parse_statement(CSV.encode("utf-8"))
    broken = parse_statement(CSV.encode("utf-8").decode("latin-1").encode("utf-8"))
    for lines in (good, broken):
        assert [ln.kind for ln in lines] == ["deposit", "deposit", "interest", "withdrawal", "withdrawal", "interest"]
        assert lines[0].date == date(2026, 9, 5) and lines[0].amount == 1500 and lines[-1].balance == 1450.08
        assert lines[-1].date == date(2026, 10, 1) and lines[3].amount == -50
    assert broken[0].description == 'Depósito en "Cuenta Remunerada"'


def test_statement_is_source_of_truth_and_matches_transfers(client, db):
    acc, ahorro = _setup(db)
    r = _upload(client, acc, CSV)
    assert r.status_code == 303 and "/review" in r.headers["location"]
    db.expire_all()
    lines = db.query(SavingsStatementLine).order_by(SavingsStatementLine.id).all()
    assert len(lines) == 6
    # El depósito de 200 € se empareja con el «Ahorro» de Caixabank; el de 1.500 € queda por revisar.
    dep200 = next(ln for ln in lines if ln.amount == 200)
    assert dep200.transaction_id == ahorro.id and dep200.classification == "caixabank"
    acc = db.get(SavingsAccount, acc.id)
    assert acc.opening_date == date(2026, 9, 5) and acc.opening_balance == 0
    s = summary(db, acc, ref=date(2026, 10, 1))
    # Saldo = el del extracto (la Residencia de junio queda antes de la apertura y no se suma dos veces).
    assert s.balance == 1450.08 and s.statement_until == date(2026, 10, 1)
    assert s.unclassified == 3 and s.total_deposits == 1700
    assert "sin clasificar" in client.get("/savings").text
    assert "Revisar extracto" in client.get(f"/savings/{acc.id}/review").text


def test_classify_and_reimport_without_duplicates(client, db):
    acc, _ = _setup(db)
    _upload(client, acc, CSV)
    db.expire_all()
    by_amount = {ln.amount: ln for ln in db.query(SavingsStatementLine)}
    client.post(f"/savings/{acc.id}/lines/{by_amount[1500].id}", data={"classification": "external"})
    client.post(f"/savings/{acc.id}/lines/{by_amount[-50].id}", data={"classification": "expense", "label": "Taller"})
    r = client.post(f"/savings/{acc.id}/lines/{by_amount[-200].id}", data={"classification": "lent", "label": "Hermano"})
    assert "Clasificado" in r.text
    bad = client.post(f"/savings/{acc.id}/lines/{by_amount[-200].id}", data={"classification": "external"})
    assert bad.status_code == 422
    db.expire_all()
    s = summary(db, db.get(SavingsAccount, acc.id), ref=date(2026, 10, 1))
    assert (s.unclassified, s.total_expenses, s.total_lent) == (0, 50, 200)
    # Reimportar el mismo extracto (más una línea nueva) no duplica nada ni pierde la clasificación.
    more = CSV + '2 Oct 2026,Interés neto pagado a Cuenta Remunerada del 2 oct 2026,1.15%,1.15%,"0,03€",,"1.450,11€"\n'
    r = _upload(client, acc, more)
    assert "1 líneas nuevas" in _loc(r) and "6 ya importadas" in _loc(r)
    db.expire_all()
    assert db.query(SavingsStatementLine).count() == 7
    assert summary(db, db.get(SavingsAccount, acc.id), ref=date(2026, 10, 2)).balance == 1450.11
    assert summary(db, db.get(SavingsAccount, acc.id), ref=date(2026, 10, 2)).total_lent == 200


def test_transfers_after_statement_date_still_count(db, client):
    acc, _ = _setup(db)
    _upload(client, acc, CSV)
    db.add(Transaction(date=date(2026, 10, 3), period_year=2026, period_month=10, name="Ahorro", amount=-300,
                       is_settled=True, savings_account_id=acc.id))
    db.commit()
    s = summary(db, db.get(SavingsAccount, acc.id), ref=date(2026, 10, 3))
    assert 1750.08 <= s.balance < 1750.2  # 1.450,08 del extracto + 300 + intereses estimados de 2 días


def test_rejects_non_statement_files(client, db):
    acc, _ = _setup(db)
    r = _upload(client, acc, "a,b,c\n1,2,3\n")
    assert r.status_code == 303 and "No se ha podido importar" in _loc(r)
