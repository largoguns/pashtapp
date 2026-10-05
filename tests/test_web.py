from datetime import date

from app.models import Category, Loan, Transaction
from app.services.auth import create_api_key
from app.services.loans import create_loan


def test_login_required(anon):
    r = anon.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = anon.patch("/transactions/1/toggle-settled", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/login"
    assert anon.post("/login", data={"username": "admin", "password": "mal"}).status_code == 401


def test_pages_render(client):
    for url in ["/", "/?y=2026&m=2", "/loans", "/analysis/categories", "/analysis/categories?y=2026&m=3",
                "/analysis/matrix", "/analysis/matrix?y=2025", "/analysis/utility", "/settings", "/settings?tab=categories", "/settings?tab=templates",
                "/settings?tab=utility", "/settings?tab=api", "/settings?tab=data", "/manifest.json", "/sw.js", "/healthz"]:
        assert client.get(url).status_code == 200, url


def test_quick_add_and_toggle(client, db):
    cat = db.query(Category).filter_by(name="Comida").one()
    r = client.post("/transactions", data={"amount": "12,50", "category_id": str(cat.id), "mode": "pending",
                                           "view_y": "2026", "view_m": "10", "op_date": "2026-10-05"})
    assert r.status_code == 200 and 'id="mobile-header"' in r.text and "hx-swap-oob" in r.text
    tx = db.query(Transaction).one()
    assert (tx.amount, tx.name, tx.is_settled, tx.period) == (-12.5, "Comida", False, (2026, 10))
    r = client.patch(f"/transactions/{tx.id}/toggle-settled?view_y=2026&view_m=10")
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Transaction, tx.id).is_settled


def test_quick_expense_api(client, db):
    token = create_api_key(db, "test")
    r = client.post("/api/v1/quick-expense", json={"text": "Pago con tarjeta de 42,30 EUR en REPSOL..."},
                    headers={"X-API-Key": token})
    assert r.status_code == 201, r.text
    assert r.json()["amount"] == -42.3 and r.json()["category"] == "Gasolina"
    r = client.post("/api/v1/quick-expense", headers={"X-API-Key": token},
                    json={"amount": 14.50, "concept": "Mercadona", "category": "Comida", "is_settled": False})
    assert r.status_code == 201 and r.json()["category"] == "Comida"
    assert client.post("/api/v1/quick-expense", json={"amount": 1}, headers={"X-API-Key": "nope"}).status_code == 401
    assert client.post("/api/v1/quick-expense", json={"text": "hola"}, headers={"X-API-Key": token}).status_code == 422


def test_simulator_endpoint(client, db):
    loan = create_loan(db, name="Placas", initial_capital=5500, annual_interest_rate=6.45, term_months=84,
                       start_date=date(2022, 12, 5), mark_paid_until=date(2026, 10, 5))
    db.commit()
    r = client.post(f"/api/v1/loans/{loan.id}/simulate-prepayment",
                    json={"prepayment_amount": 1000, "strategy": "REDUCE_FEE"})
    assert r.status_code == 200 and r.json()["interest_savings"] > 0
    r = client.post(f"/api/v1/loans/{loan.id}/simulate-prepayment", headers={"HX-Request": "true"},
                    data={"prepayment_amount": "1000", "strategy": "REDUCE_TERM"})
    assert "Ahorro neto de intereses" in r.text
    assert client.post(f"/api/v1/loans/{loan.id}/simulate-prepayment",
                       json={"prepayment_amount": 99999}).status_code == 422
    assert db.query(Loan).count() == 1


def test_exports_and_backup(client, db):
    from openpyxl import load_workbook
    import io

    db.add(Transaction(date=date(2026, 3, 1), period_year=2026, period_month=3, name="Algo", amount=-5)); db.commit()
    r = client.get("/export/transactions.csv?year=2026&month=3")
    assert r.status_code == 200 and "Algo" in r.text and "-5,00" in r.text
    r = client.get("/export/full-backup.xlsx")
    wb = load_workbook(io.BytesIO(r.content))
    assert "transactions" in wb.sheetnames and "users" not in wb.sheetnames
    r = client.post("/backups/run", follow_redirects=False)
    assert r.status_code == 303
    assert "pashtapp_" in client.get("/settings?tab=data").text


def test_password_hash_accepts_base64(monkeypatch):
    import base64

    import pytest

    from app.config import _decode_password_hash
    from app.services.auth import hash_password, verify_password

    h = hash_password("clave")
    b64 = base64.b64encode(h.encode()).decode()
    assert "$" not in b64
    assert _decode_password_hash(b64) == h and verify_password("clave", _decode_password_hash(b64))
    assert _decode_password_hash(h) == h
    assert _decode_password_hash(f"'{h}'") == h
    assert _decode_password_hash(None) is None
    # Un valor que no es hash ni base64 de hash falla al arrancar, no en el login.
    with pytest.raises(RuntimeError):
        _decode_password_hash("no-es-un-hash")


def test_new_movement_goes_to_viewed_month_and_period_is_editable(client, db):
    client.post("/transactions", data={"amount": "5", "mode": "settled", "view_y": "2026", "view_m": "11",
                                       "op_date": "2026-10-29"})
    tx = db.query(Transaction).one()
    assert tx.period == (2026, 11) and tx.date == date(2026, 10, 29)
    r = client.post(f"/transactions/{tx.id}", data={"name": "x", "amount": "5", "op_date": "2026-10-29",
                                                    "period": "2026-10", "view_y": "2026", "view_m": "10"})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Transaction, tx.id).period == (2026, 10)


def test_schema_upgrade_backfills_period(tmp_path, monkeypatch):
    import sqlite3

    from sqlalchemy import create_engine

    import app.database as database

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, date DATE NOT NULL, name TEXT, amount REAL)")
    con.execute("INSERT INTO transactions (date, name, amount) VALUES ('2026-03-15', 'x', -1)")
    con.commit(); con.close()
    monkeypatch.setattr(database, "engine", create_engine(f"sqlite:///{path}"))
    database._upgrade_schema()
    con = sqlite3.connect(path)
    assert con.execute("SELECT period_year, period_month FROM transactions").fetchone() == (2026, 3)


def test_analysis_pages_show_data(client, db):
    from app.models import UtilityReading

    cat = db.query(Category).filter_by(name="Comida").one()
    cat.monthly_budget_limit = 100
    db.add(Transaction(date=date(2026, 3, 2), period_year=2026, period_month=3, name="Súper", amount=-85,
                       category_id=cat.id))
    db.add(UtilityReading(year=2026, month=3, amount=55.5))
    db.commit()
    r = client.get("/analysis/categories?y=2026&m=3")
    assert "Comida" in r.text and "85,00 €" in r.text and "badge-amber" in r.text
    assert "Comida" in client.get("/analysis/matrix?y=2026").text
    assert "56" in client.get("/analysis/utility").text
    # El tablero ya no incluye analítica ni préstamos.
    home = client.get("/?y=2026&m=3").text
    assert 'id="donut"' not in home and "Simular amortización" not in home


def test_month_picker_only_enables_months_with_data(client, db):
    for y, m in ((2025, 12), (2026, 3)):
        db.add(Transaction(date=date(y, m, 1), period_year=y, period_month=m, name="x", amount=-1))
    db.commit()
    html = client.get("/?y=2026&m=3").text
    assert 'data-picker-year="2025"' in html and 'data-picker-year="2026"' in html
    assert 'href="/?y=2025&m=12"' in html and 'href="/?y=2026&m=3"' in html
    assert 'href="/?y=2025&m=11"' not in html  # sin datos: no seleccionable
    assert 'href="/analysis/matrix?y=2025"' in client.get("/analysis/matrix?y=2026").text
    assert 'href="/analysis/categories?y=2025&m=12"' in client.get("/analysis/categories?y=2026&m=3").text


def test_hidden_attribute_beats_display_utilities():
    """El selector oculta las cuadrículas de otros años con [hidden]; la clase .grid no debe pisarlo."""
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "app/static/css/app.css").read_text()
    assert "[hidden]{display:none!important}" in css
