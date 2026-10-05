from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.models import Loan, LoanInstallment, Transaction
from app.services.loans import create_loan, detect_residual, generate_installments, loan_status
from app.templating import templates
from app.utils import parse_amount, today

router = APIRouter(prefix="/loans", dependencies=[Depends(require_user)])


def _loan(db: Session, loan_id: int) -> Loan:
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(404, "Préstamo no encontrado")
    return loan


@router.get("")
def loans_page(request: Request, db: Session = Depends(get_db)):
    loans = db.scalars(select(Loan).order_by(Loan.active.desc(), Loan.name)).unique().all()
    return templates.TemplateResponse(
        request, "loans.html", {"loans": [(loan, loan_status(loan)) for loan in loans], "today": today()}
    )


@router.post("")
def loan_create(
    name: str = Form(...),
    initial_capital: str = Form(...),
    annual_interest_rate: str = Form(...),
    term_months: int = Form(...),
    start_date: date = Form(...),
    monthly_fee: str | None = Form(None),
    mark_past_paid: bool = Form(False),
    db: Session = Depends(get_db),
):
    capital = parse_amount(initial_capital)
    rate = parse_amount(annual_interest_rate)
    if not capital or capital <= 0 or rate is None or rate < 0 or term_months <= 0:
        raise HTTPException(422, "Datos del préstamo no válidos")
    create_loan(
        db,
        name=name.strip(),
        initial_capital=capital,
        annual_interest_rate=rate,
        term_months=term_months,
        start_date=start_date,
        monthly_fee=parse_amount(monthly_fee) if monthly_fee else None,
        mark_paid_until=today() if mark_past_paid else None,
    )
    db.commit()
    return RedirectResponse("/loans", status_code=303)


@router.get("/{loan_id}")
def loan_detail(request: Request, loan_id: int, db: Session = Depends(get_db)):
    loan = _loan(db, loan_id)
    return templates.TemplateResponse(
        request, "loan_detail.html",
        {"loan": loan, "status": loan_status(loan), "today": today(),
         "balloon": detect_residual(list(loan.installments))},
    )


@router.get("/{loan_id}/simulator")
def simulator_modal(request: Request, loan_id: int, db: Session = Depends(get_db)):
    loan = _loan(db, loan_id)
    return templates.TemplateResponse(
        request, "partials/simulator_modal.html", {"loan": loan, "status": loan_status(loan)}
    )


@router.post("/{loan_id}/toggle-active")
def loan_toggle_active(loan_id: int, db: Session = Depends(get_db)):
    loan = _loan(db, loan_id)
    loan.active = not loan.active
    db.commit()
    return RedirectResponse("/loans", status_code=303)


@router.post("/{loan_id}/regenerate")
def loan_regenerate(loan_id: int, db: Session = Depends(get_db)):
    """Recalcula el cuadro teórico conservando como pagadas las cuotas ya vencidas."""
    loan = _loan(db, loan_id)
    if detect_residual(list(loan.installments)):
        raise HTTPException(409, "El cuadro tiene cuota final (balloon): no se puede recalcular sin perderla.")
    _unlink_transactions(db, loan)
    generate_installments(loan, paid_until=today())
    db.commit()
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@router.post("/{loan_id}/delete")
def loan_delete(loan_id: int, db: Session = Depends(get_db)):
    loan = _loan(db, loan_id)
    _unlink_transactions(db, loan)
    db.delete(loan)
    db.commit()
    return RedirectResponse("/loans", status_code=303)


@router.patch("/{loan_id}/installments/{inst_id}/toggle")
def installment_toggle(request: Request, loan_id: int, inst_id: int, db: Session = Depends(get_db)):
    inst = db.get(LoanInstallment, inst_id)
    if inst is None or inst.loan_id != loan_id:
        raise HTTPException(404)
    inst.is_paid = not inst.is_paid
    tx = db.scalar(select(Transaction).where(Transaction.loan_installment_id == inst.id))
    if tx:
        tx.is_settled = inst.is_paid
    db.commit()
    return templates.TemplateResponse(request, "partials/installment_row.html", {"i": inst, "today": today()})


def _unlink_transactions(db: Session, loan: Loan) -> None:
    ids = [i.id for i in loan.installments]
    if ids:
        for tx in db.scalars(select(Transaction).where(Transaction.loan_installment_id.in_(ids))):
            tx.loan_installment_id = None
        db.flush()
