"""API v1: ingesta rápida (Mejora 2), simulador (Mejora 3) y resumen de saldos."""
from __future__ import annotations

from dataclasses import asdict
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import is_htmx, require_api_access
from app.models import Loan
from app.services.balances import month_summary
from app.services.loans import Strategy, simulate_prepayment
from app.services.parser import DEFAULT_CATEGORY, guess_category, parse_bank_text
from app.services.transactions import PaymentMode, create_movement, get_or_create_category
from app.templating import templates
from app.utils import parse_amount
from app.views import resolve_month

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_access)])


class QuickExpenseIn(BaseModel):
    amount: float | None = Field(default=None, description="Importe del gasto (positivo)")
    concept: str | None = None
    category: str | None = None
    is_settled: bool = False
    date: dt.date | None = None
    mode: PaymentMode = PaymentMode.NOW
    text: str | None = Field(default=None, description="Texto libre de SMS/notificación bancaria")

    @model_validator(mode="after")
    def _need_something(self):
        if self.amount is None and not self.text:
            raise ValueError("Envía 'amount' (JSON estructurado) o 'text' (SMS bancario)")
        return self


@router.post("/quick-expense", status_code=201)
def quick_expense(payload: QuickExpenseIn, db: Session = Depends(get_db)):
    amount, concept, category = payload.amount, payload.concept, payload.category
    if payload.text:
        parsed = parse_bank_text(payload.text)
        amount = amount if amount is not None else parsed.amount
        concept = concept or parsed.concept
        category = category or parsed.category
    if amount is None or amount == 0:
        raise HTTPException(422, "No se ha podido detectar el importe")
    if not category:
        category = guess_category(concept or "") if concept else DEFAULT_CATEGORY

    cat = get_or_create_category(db, category)
    [tx] = create_movement(
        db,
        amount=abs(amount),
        name=concept or cat.name,
        category_id=cat.id,
        op_date=payload.date,
        mode=payload.mode if payload.mode is not PaymentMode.SPLIT else PaymentMode.NOW,
        is_settled=payload.is_settled,
        notes="[quick-expense]",
    )
    db.commit()
    return {
        "id": tx.id,
        "date": tx.date.isoformat(),
        "settlement_date": tx.settlement_date.isoformat() if tx.settlement_date else None,
        "name": tx.name,
        "amount": tx.amount,
        "category": cat.name,
        "is_settled": tx.is_settled,
    }


@router.get("/summary")
def summary(year: int | None = None, month: int | None = None, db: Session = Depends(get_db)):
    y, m = resolve_month(year, month)
    return asdict(month_summary(db, y, m))


@router.post("/loans/{loan_id}/simulate-prepayment")
async def simulate(request: Request, loan_id: int, db: Session = Depends(get_db)):
    """Acepta JSON ``{"prepayment_amount", "strategy"}`` o formulario (modal HTMX)."""
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
    else:
        data = dict(await request.form())
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise HTTPException(404, "Préstamo no encontrado")

    error, result = None, None
    try:
        amount = parse_amount(data.get("prepayment_amount"))
        if amount is None:
            raise ValueError("Indica el capital a amortizar.")
        strategy = Strategy(str(data.get("strategy", Strategy.REDUCE_TERM.value)).upper())
        result = simulate_prepayment(loan, amount, strategy)
    except ValueError as exc:
        error = str(exc)

    if is_htmx(request):
        return templates.TemplateResponse(
            request, "partials/simulation_result.html", {"result": result, "error": error, "loan": loan}
        )
    if error:
        raise HTTPException(422, error)
    return result.to_dict()
