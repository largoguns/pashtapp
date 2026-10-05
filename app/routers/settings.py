"""Ajustes: saldo inicial, categorías, plantillas fijas, luz, API keys y copias."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.models import ApiKey, Category, RecurringTemplate, Transaction, UtilityReading
from app.services.auth import create_api_key
from app.services.backup import list_backups, run_backup
from app.services.periods import get_start_day, set_start_day
from app.services.settings_store import get_opening_balance, set_opening_balance
from app.templating import templates
from app.utils import parse_amount, today

router = APIRouter(dependencies=[Depends(require_user)])


def _back(section: str, msg: str | None = None) -> RedirectResponse:
    url = f"/settings?tab={section}"
    if msg:
        url += f"&msg={quote(msg)}"
    return RedirectResponse(url, status_code=303)


def _opt_float(raw: str | None) -> float | None:
    return parse_amount(raw) if raw not in (None, "") else None


def _opt_int(raw: str | None) -> int | None:
    return int(raw) if raw and raw.strip().isdigit() else None


@router.get("/settings")
def settings_page(request: Request, tab: str = "general", msg: str | None = None, new_key: str | None = None,
                  db: Session = Depends(get_db)):
    cats = db.scalars(select(Category).order_by(Category.name)).all()
    usage = dict(db.execute(select(Transaction.category_id, func.count()).group_by(Transaction.category_id)).all())
    ctx = {
        "tab": tab,
        "msg": msg,
        "new_key": new_key,
        "opening": get_opening_balance(db),
        "start_day": get_start_day(db),
        "categories": cats,
        "usage": usage,
        "templates_list": db.scalars(
            select(RecurringTemplate).order_by(RecurringTemplate.is_income.desc(), RecurringTemplate.day_of_month)
        ).unique().all(),
        "readings": db.scalars(
            select(UtilityReading).order_by(UtilityReading.year.desc(), UtilityReading.month.desc())
        ).all(),
        "api_keys": db.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())).all(),
        "backups": list_backups(),
        "today": today(),
    }
    return templates.TemplateResponse(request, "settings.html", ctx)


# --- General ----------------------------------------------------------------

@router.post("/settings/opening")
def save_opening(amount: str = Form(...), year: int = Form(...), month: int = Form(...),
                 db: Session = Depends(get_db)):
    value = parse_amount(amount)
    if value is None or not 1 <= month <= 12:
        raise HTTPException(422, "Saldo inicial no válido")
    set_opening_balance(db, value, year, month)
    db.commit()
    return _back("general", "Saldo inicial guardado")


@router.post("/settings/period")
def save_period(start_day: int = Form(...), db: Session = Depends(get_db)):
    if not 1 <= start_day <= 28:
        raise HTTPException(422, "El día debe estar entre 1 y 28")
    set_start_day(db, start_day)
    db.commit()
    return _back("general", "Inicio del mes contable guardado")


# --- Categorías ---------------------------------------------------------------

@router.post("/settings/categories")
def category_save(
    category_id: str | None = Form(None),
    name: str = Form(...),
    icon: str = Form("tag"),
    color_hex: str = Form("#64748b"),
    monthly_budget_limit: str | None = Form(None),
    is_fixed_default: bool = Form(False),
    db: Session = Depends(get_db),
):
    cid = _opt_int(category_id)
    cat = db.get(Category, cid) if cid else Category()
    if cat is None:
        raise HTTPException(404)
    cat.name = name.strip()
    cat.icon = icon.strip() or "tag"
    cat.color_hex = color_hex
    cat.monthly_budget_limit = _opt_float(monthly_budget_limit)
    cat.is_fixed_default = is_fixed_default
    db.add(cat)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _back("categories", f"Ya existe una categoría «{name}»")
    return _back("categories", "Categoría guardada")


@router.post("/settings/categories/{cat_id}/delete")
def category_delete(cat_id: int, db: Session = Depends(get_db)):
    cat = db.get(Category, cat_id)
    if cat:
        db.execute(update(Transaction).where(Transaction.category_id == cat_id).values(category_id=None))
        db.execute(update(RecurringTemplate).where(RecurringTemplate.category_id == cat_id).values(category_id=None))
        db.delete(cat)
        db.commit()
    return _back("categories", "Categoría eliminada")


# --- Plantillas de fijos ---------------------------------------------------------

@router.post("/settings/templates")
def template_save(
    template_id: str | None = Form(None),
    name: str = Form(...),
    default_amount: str = Form(...),
    category_id: str | None = Form(None),
    day_of_month: int = Form(1),
    is_income: bool = Form(False),
    active: bool = Form(False),
    db: Session = Depends(get_db),
):
    tid = _opt_int(template_id)
    tpl = db.get(RecurringTemplate, tid) if tid else RecurringTemplate()
    if tpl is None:
        raise HTTPException(404)
    amount = parse_amount(default_amount)
    if amount is None:
        raise HTTPException(422, "Importe no válido")
    tpl.name = name.strip()
    tpl.default_amount = abs(amount)
    tpl.category_id = _opt_int(category_id)
    tpl.day_of_month = max(1, min(31, day_of_month))
    tpl.is_income = is_income
    tpl.active = active
    db.add(tpl)
    db.commit()
    return _back("templates", "Plantilla guardada. Usa «Reabrir mes» para aplicarla al mes actual.")


@router.post("/settings/templates/{tpl_id}/delete")
def template_delete(tpl_id: int, db: Session = Depends(get_db)):
    tpl = db.get(RecurringTemplate, tpl_id)
    if tpl:
        db.execute(update(Transaction).where(Transaction.template_id == tpl_id).values(template_id=None))
        db.delete(tpl)
        db.commit()
    return _back("templates", "Plantilla eliminada")


# --- Luz ----------------------------------------------------------------------------

@router.post("/settings/utility")
def utility_save(year: int = Form(...), month: int = Form(...), amount: str = Form(...),
                 kwh: str | None = Form(None), notes: str | None = Form(None), db: Session = Depends(get_db)):
    value = parse_amount(amount)
    if value is None or not 1 <= month <= 12:
        raise HTTPException(422, "Lectura no válida")
    row = db.scalar(select(UtilityReading).where(UtilityReading.year == year, UtilityReading.month == month))
    row = row or UtilityReading(year=year, month=month)
    row.amount, row.kwh, row.notes = value, _opt_float(kwh), notes or None
    db.add(row)
    db.commit()
    return _back("utility", "Lectura guardada")


@router.post("/settings/utility/{reading_id}/delete")
def utility_delete(reading_id: int, db: Session = Depends(get_db)):
    row = db.get(UtilityReading, reading_id)
    if row:
        db.delete(row)
        db.commit()
    return _back("utility", "Lectura eliminada")


# --- API keys -------------------------------------------------------------------------

@router.post("/settings/api-keys")
def api_key_create(request: Request, label: str = Form(...), db: Session = Depends(get_db)):
    token = create_api_key(db, label.strip() or "Atajo")
    # El token sólo se muestra una vez; no se guarda en claro.
    return settings_page(request, tab="api", msg="Copia el token ahora: no se volverá a mostrar.",
                         new_key=token, db=db)


@router.post("/settings/api-keys/{key_id}/delete")
def api_key_delete(key_id: int, db: Session = Depends(get_db)):
    key = db.get(ApiKey, key_id)
    if key:
        db.delete(key)
        db.commit()
    return _back("api", "API key revocada")


# --- Copias -------------------------------------------------------------------------------

@router.post("/backups/run")
def backup_now():
    path = run_backup()
    return _back("data", f"Copia creada: {path.name}")
