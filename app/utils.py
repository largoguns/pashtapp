"""Utilidades de fechas, importes y formato."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings

MONTH_NAMES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]
MONTH_SHORT = [m[:3] for m in MONTH_NAMES]


def today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def add_months(d: date, months: int, day: int | None = None) -> date:
    """Suma ``months`` meses, ajustando el día al último del mes si no existe."""
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day or d.day, last))


def month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def next_month_start(year: int, month: int) -> date:
    return add_months(date(year, month, 1), 1, day=1)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    d = add_months(date(year, month, 1), delta, day=1)
    return d.year, d.month


def clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, max(1, min(day, calendar.monthrange(year, month)[1])))


_AMOUNT_CLEAN = re.compile(r"[^\d,.\-]")


def parse_amount(raw: str | float | int | None) -> float | None:
    """Interpreta importes en formato español o inglés: ``1.234,56``, ``1234.56``, ``14,5``."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = _AMOUNT_CLEAN.sub("", str(raw).strip())
    if not s or s in {"-", ",", "."}:
        return None
    if "," in s and "." in s:
        # El último separador es el decimal.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def fmt_eur(value: float | None, sign: bool = False) -> str:
    if value is None:
        return "—"
    txt = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    prefix = "-" if value < 0 else ("+" if sign and value > 0 else "")
    return f"{prefix}{txt} €"


def fmt_date(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else ""
