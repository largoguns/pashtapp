"""Ajustes clave/valor persistidos en ``app_settings``."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import AppSetting
from app.utils import today


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(AppSetting, key)
    return row.value if row else default


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


@dataclass
class OpeningBalance:
    amount: float
    year: int
    month: int


def get_opening_balance(db: Session) -> OpeningBalance:
    """Saldo inicial configurado y el mes desde el que aplica (§4.1)."""
    t = today()
    return OpeningBalance(
        amount=float(get_setting(db, "opening_balance", "0") or 0),
        year=int(get_setting(db, "opening_year", str(t.year)) or t.year),
        month=int(get_setting(db, "opening_month", "1") or 1),
    )


def set_opening_balance(db: Session, amount: float, year: int, month: int) -> None:
    set_setting(db, "opening_balance", f"{amount:.2f}")
    set_setting(db, "opening_year", str(year))
    set_setting(db, "opening_month", str(month))
