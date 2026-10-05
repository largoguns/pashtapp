from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.models import Transaction
from app.services.transactions import PaymentMode, create_movement, set_settled
from app.templating import templates
from app.utils import parse_amount
from app.views import categories, render_month_fragments, resolve_month

router = APIRouter(prefix="/transactions", dependencies=[Depends(require_user)])


def _get_tx(db: Session, tx_id: int) -> Transaction:
    tx = db.get(Transaction, tx_id)
    if tx is None:
        raise HTTPException(404, "Movimiento no encontrado")
    return tx


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(422, f"Fecha no válida: {raw}")


def _opt_int(raw: str | None) -> int | None:
    return int(raw) if raw and raw.strip().isdigit() else None


def _amount(raw: str) -> float:
    value = parse_amount(raw)
    if value is None or value == 0:
        raise HTTPException(422, "Importe no válido")
    return abs(value)


@router.post("")
def create(
    request: Request,
    amount: str = Form(...),
    name: str = Form(""),
    category_id: str | None = Form(None),
    op_date: str | None = Form(None),
    mode: str = Form("settled"),
    installments: int = Form(3),
    is_settled: bool = Form(False),
    kind: str = Form("expense"),
    is_fixed: bool = Form(False),
    notes: str | None = Form(None),
    view_y: str | None = Form(None),
    view_m: str | None = Form(None),
    db: Session = Depends(get_db),
):
    value = _amount(amount)
    category_id = _opt_int(category_id)
    # El selector de estado añade "settled"/"pending" como variantes del gasto inmediato.
    if mode in ("settled", "pending"):
        is_settled, mode = mode == "settled", PaymentMode.NOW.value
    try:
        pay_mode = PaymentMode(mode)
    except ValueError:
        raise HTTPException(422, f"Modo de pago no válido: {mode}")
    cat_name = next((c.name for c in categories(db) if c.id == category_id), None)
    txs = create_movement(
        db,
        amount=value,
        name=name or cat_name or "Gasto",
        category_id=category_id,
        op_date=_parse_date(op_date),
        mode=pay_mode,
        is_settled=is_settled,
        is_income=kind == "income",
        is_fixed=is_fixed,
        installments=max(2, min(installments, 60)) if pay_mode is PaymentMode.SPLIT else 1,
        notes=notes or None,
    )
    db.commit()
    year, month = resolve_month(view_y, view_m)
    msg = f"Añadido: {txs[0].name}" + (f" en {len(txs)} cuotas" if len(txs) > 1 else "")
    return render_month_fragments(request, db, year, month, toast=msg)


@router.patch("/{tx_id}/toggle-settled")
def toggle_settled(request: Request, tx_id: int, view_y: str | None = None, view_m: str | None = None,
                   db: Session = Depends(get_db)):
    tx = _get_tx(db, tx_id)
    set_settled(db, tx, not tx.is_settled)
    db.commit()
    year, month = resolve_month(view_y or tx.booking_date.year, view_m or tx.booking_date.month)
    return render_month_fragments(request, db, year, month)


@router.get("/{tx_id}/edit")
def edit_form(request: Request, tx_id: int, view_y: str | None = None, view_m: str | None = None,
              db: Session = Depends(get_db)):
    tx = _get_tx(db, tx_id)
    return templates.TemplateResponse(
        request, "partials/tx_edit.html",
        {"tx": tx, "categories": categories(db), "view_y": view_y, "view_m": view_m},
    )


@router.post("/{tx_id}")
def update(
    request: Request,
    tx_id: int,
    name: str = Form(...),
    amount: str = Form(...),
    kind: str = Form("expense"),
    op_date: str = Form(...),
    settlement_date: str | None = Form(None),
    category_id: str | None = Form(None),
    is_fixed: bool = Form(False),
    is_settled: bool = Form(False),
    notes: str | None = Form(None),
    view_y: str | None = Form(None),
    view_m: str | None = Form(None),
    db: Session = Depends(get_db),
):
    tx = _get_tx(db, tx_id)
    value = _amount(amount)
    tx.name = name.strip() or tx.name
    tx.is_income = kind == "income"
    tx.amount = value if tx.is_income else -value
    tx.date = _parse_date(op_date) or tx.date
    tx.settlement_date = _parse_date(settlement_date)
    tx.category_id = _opt_int(category_id)
    tx.is_fixed = is_fixed
    tx.notes = notes or None
    set_settled(db, tx, is_settled)
    db.commit()
    year, month = resolve_month(view_y, view_m)
    return render_month_fragments(request, db, year, month, toast="Movimiento actualizado")


@router.delete("/{tx_id}")
def remove(request: Request, tx_id: int, scope: str = "one", view_y: str | None = None,
           view_m: str | None = None, db: Session = Depends(get_db)):
    tx = _get_tx(db, tx_id)
    if scope == "group" and tx.installment_group_id:
        db.execute(delete(Transaction).where(Transaction.installment_group_id == tx.installment_group_id))
        msg = "Compra fraccionada eliminada"
    else:
        db.delete(tx)
        msg = "Movimiento eliminado"
    db.commit()
    year, month = resolve_month(view_y, view_m)
    return render_month_fragments(request, db, year, month, toast=msg)
