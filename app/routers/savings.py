"""Sección Ahorro: cuentas de ahorro (Revolut), cuadres, retiradas y evolución."""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.models import Category, SavingsAccount, SavingsMovement, SavingsStatementLine, Transaction
from app.services.periods import current_period
from app.services.savings import accounts, link_category_history, reconcile, statement_lines, summary
from app.services.statement_import import StatementError, classify, import_statement, match_candidates, parse_statement
from app.services.transactions import create_movement, get_or_create_category
from app.templating import templates
from app.utils import fmt_eur, parse_amount, today

router = APIRouter(prefix="/savings", dependencies=[Depends(require_user)])

TRANSFER_CATEGORY = "Traspasos"


def _account(db: Session, account_id: int) -> SavingsAccount:
    acc = db.get(SavingsAccount, account_id)
    if acc is None:
        raise HTTPException(404, "Cuenta de ahorro no encontrada")
    return acc


def _amount(raw: str, *, allow_negative=False) -> float:
    value = parse_amount(raw)
    if value is None or (not allow_negative and value <= 0):
        raise HTTPException(422, "Importe no válido")
    return value


def _date(raw: str | None) -> date:
    try:
        return date.fromisoformat(raw) if raw else today()
    except ValueError:
        raise HTTPException(422, f"Fecha no válida: {raw}")


def _back(account_id: int | None = None, msg: str | None = None) -> RedirectResponse:
    url = f"/savings?acc={account_id}" if account_id else "/savings"
    if msg:
        url += ("&" if "?" in url else "?") + f"msg={quote(msg)}"
    return RedirectResponse(url, status_code=303)


@router.get("")
def savings_page(request: Request, acc: int | None = None, msg: str | None = None, db: Session = Depends(get_db)):
    accs = accounts(db)
    cats = list(db.scalars(select(Category).order_by(Category.name)))
    if not accs:
        ahorro = next((c for c in cats if c.name.lower() == "ahorro"), None)
        first = None
        if ahorro:
            first = db.scalar(select(func.min(Transaction.date)).where(Transaction.category_id == ahorro.id))
        return templates.TemplateResponse(request, "savings/setup.html", {
            "categories": cats, "default_category": ahorro,
            "default_date": date(first.year, first.month, 1) if first else today(),
        })
    account = next((a for a in accs if a.id == acc), accs[0])
    s = summary(db, account)
    return templates.TemplateResponse(request, "savings/index.html", {
        "accounts": accs, "account": account, "s": s, "msg": msg, "today": today(),
        "linked_categories": [c for c in cats if c.savings_account_id == account.id],
        "categories": cats,
        "chart": {
            "labels": [r.label for r in s.months] + [label for label, _ in s.projection],
            "real": [r.balance for r in s.months] + [None] * len(s.projection),
            "projection": [None] * (len(s.months) - 1) + [s.months[-1].balance if s.months else s.balance]
                          + [v for _, v in s.projection],
        },
        "months": [asdict(r) | {"label": r.label} for r in reversed(s.months)],
    })


@router.post("")
def create_account(
    name: str = Form(...),
    annual_rate: str = Form("0"),
    opening_balance: str = Form("0"),
    opening_date: str | None = Form(None),
    category_id: str | None = Form(None),
    link_history: bool = Form(False),
    db: Session = Depends(get_db),
):
    acc = SavingsAccount(
        name=name.strip() or "Ahorro", annual_rate=parse_amount(annual_rate) or 0.0,
        opening_balance=parse_amount(opening_balance) or 0.0, opening_date=_date(opening_date), active=True,
    )
    db.add(acc)
    db.flush()
    linked = 0
    if category_id and category_id.isdigit():
        cat = db.get(Category, int(category_id))
        if cat:
            cat.savings_account_id = acc.id
            if link_history:
                linked = link_category_history(db, cat, acc)
    db.commit()
    return _back(acc.id, f"Cuenta creada. {linked} movimientos vinculados como aportaciones." if linked else "Cuenta creada.")


@router.post("/{account_id}/settings")
def update_account(account_id: int, name: str = Form(...), annual_rate: str = Form("0"),
                   opening_balance: str = Form("0"), opening_date: str = Form(...),
                   category_ids: list[str] = Form(default=[]), db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    acc.name = name.strip() or acc.name
    acc.annual_rate = parse_amount(annual_rate) or 0.0
    acc.opening_balance = parse_amount(opening_balance) or 0.0
    acc.opening_date = _date(opening_date)
    wanted = {int(c) for c in category_ids if c.isdigit()}
    for cat in db.scalars(select(Category)):
        if cat.id in wanted:
            cat.savings_account_id = acc.id
        elif cat.savings_account_id == acc.id:
            cat.savings_account_id = None
    db.commit()
    return _back(acc.id, "Cuenta actualizada")


@router.post("/{account_id}/reconcile")
def do_reconcile(account_id: int, real_balance: str = Form(...), on: str | None = Form(None),
                 db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    mv = reconcile(db, acc, _amount(real_balance, allow_negative=True), _date(on))
    db.commit()
    return _back(acc.id, f"Cuadrado: {mv.amount:+.2f} € de intereses registrados".replace(".", ","))


@router.post("/{account_id}/expense")
def direct_expense(account_id: int, amount: str = Form(...), name: str = Form(...), on: str | None = Form(None),
                   category_id: str | None = Form(None), notes: str | None = Form(None),
                   db: Session = Depends(get_db)):
    """Gasto pagado directamente con la cuenta de ahorro (no pasa por la cuenta principal)."""
    acc = _account(db, account_id)
    db.add(SavingsMovement(account_id=acc.id, date=_date(on), kind="expense", amount=-_amount(amount),
                           name=name.strip() or "Gasto", notes=notes or None,
                           category_id=int(category_id) if category_id and category_id.isdigit() else None))
    db.commit()
    return _back(acc.id, "Gasto con la cuenta de ahorro registrado")


@router.post("/{account_id}/withdraw")
def withdraw(account_id: int, amount: str = Form(...), on: str | None = Form(None), notes: str | None = Form(None),
             db: Session = Depends(get_db)):
    """Traspaso de la cuenta de ahorro a la principal: entra como movimiento en Caixabank."""
    acc = _account(db, account_id)
    when = _date(on)
    cat = get_or_create_category(db, TRANSFER_CATEGORY, icon="🔁")
    [tx] = create_movement(db, amount=_amount(amount), name=f"Traspaso desde {acc.name}", category_id=cat.id,
                           op_date=when, is_income=True, is_settled=True, notes=notes or None,
                           period=current_period(db) if when >= today() else None)
    tx.savings_account_id = acc.id
    db.commit()
    return _back(acc.id, f"Traspaso a la cuenta principal registrado ({tx.period_month:02d}/{tx.period_year})")


@router.post("/{account_id}/movements/{movement_id}/delete")
def delete_movement(account_id: int, movement_id: int, db: Session = Depends(get_db)):
    mv = db.get(SavingsMovement, movement_id)
    if mv and mv.account_id == account_id:
        db.delete(mv)
        db.commit()
    return _back(account_id, "Movimiento eliminado")


@router.get("/{account_id}/candidates")
def candidates(request: Request, account_id: int, q: str = "", db: Session = Depends(get_db)):
    """Movimientos de la cuenta principal que se pueden vincular como aportaciones."""
    acc = _account(db, account_id)
    query = select(Transaction).where(Transaction.is_income.is_(False), Transaction.date >= acc.opening_date)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.join(Category, Category.id == Transaction.category_id, isouter=True).where(
            or_(Transaction.name.ilike(like), Category.name.ilike(like)))
    else:
        query = query.where(Transaction.savings_account_id == acc.id)
    txs = db.scalars(query.order_by(Transaction.date.desc()).limit(40)).unique().all()
    return templates.TemplateResponse(request, "savings/candidates.html", {"account": acc, "txs": txs, "q": q})


@router.post("/{account_id}/link/{tx_id}")
def toggle_link(request: Request, account_id: int, tx_id: int, db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    tx = db.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404)
    tx.savings_account_id = None if tx.savings_account_id == acc.id else acc.id
    db.commit()
    return templates.TemplateResponse(request, "savings/candidate_row.html", {"account": acc, "tx": tx})


# --- Extracto ----------------------------------------------------------------------

MAX_STATEMENT_BYTES = 5 * 1024 * 1024


@router.post("/{account_id}/statement")
async def upload_statement(account_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    data = await file.read(MAX_STATEMENT_BYTES + 1)
    if len(data) > MAX_STATEMENT_BYTES:
        return _back(acc.id, "El extracto supera los 5 MB")
    try:
        lines = parse_statement(data)
    except StatementError as exc:
        return _back(acc.id, f"No se ha podido importar: {exc}")
    report = import_statement(db, acc, lines)
    db.commit()
    msg = (f"Extracto importado hasta el {report.last:%d/%m/%Y} (saldo {fmt_eur(report.last_balance)}): "
           f"{report.new} líneas nuevas, {report.duplicates} ya importadas, {report.matched} emparejadas"
           f" y {report.pending} por revisar.")
    if report.warnings:
        msg += " Atención: " + "; ".join(report.warnings[:3])
    return RedirectResponse(f"/savings/{acc.id}/review?msg={quote(msg)}" if report.pending
                            else f"/savings?acc={acc.id}&msg={quote(msg)}", status_code=303)


def _line_ctx(db: Session, acc: SavingsAccount, line: SavingsStatementLine) -> dict:
    cands = match_candidates(db, acc, line) or match_candidates(db, acc, line, timedelta(days=15), exact=False)[:8]
    return {"account": acc, "line": line, "candidates": cands,
            "categories": list(db.scalars(select(Category).order_by(Category.name)))}


@router.get("/{account_id}/review")
def review(request: Request, account_id: int, msg: str | None = None, show: str = "pending",
           db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    lines = [ln for ln in statement_lines(db, acc) if ln.kind != "interest"]
    if show == "pending":
        lines = [ln for ln in lines if ln.classification is None]
    rows = [_line_ctx(db, acc, ln) for ln in lines]
    return templates.TemplateResponse(request, "savings/review.html", {
        "account": acc, "rows": rows, "msg": msg, "show": show,
        "pending": sum(1 for ln in statement_lines(db, acc) if ln.kind != "interest" and ln.classification is None),
    })


@router.post("/{account_id}/lines/{line_id}")
def classify_line(request: Request, account_id: int, line_id: int, classification: str = Form(...),
                  transaction_id: str | None = Form(None), label: str | None = Form(None),
                  category_id: str | None = Form(None), db: Session = Depends(get_db)):
    acc = _account(db, account_id)
    line = db.get(SavingsStatementLine, line_id)
    if line is None or line.account_id != acc.id:
        raise HTTPException(404)
    try:
        classify(db, acc, line, classification,
                 transaction_id=int(transaction_id) if transaction_id and transaction_id.isdigit() else None,
                 label=label, category_id=int(category_id) if category_id and category_id.isdigit() else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    db.commit()
    return templates.TemplateResponse(request, "savings/review_row.html", _line_ctx(db, acc, line))
