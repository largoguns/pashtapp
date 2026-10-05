"""Entorno Jinja2 compartido con filtros de formato."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.utils import MONTH_NAMES, MONTH_SHORT, fmt_date, fmt_eur

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Nombre de icono -> emoji. Si el valor no está en el mapa se muestra tal cual (permite emojis directos).
ICONS = {
    "tag": "🏷️", "cart": "🛒", "food": "🍽️", "fuel": "⛽", "car": "🚗", "home": "🏠", "bolt": "⚡",
    "water": "💧", "phone": "📱", "wifi": "📶", "health": "💊", "gift": "🎁", "kids": "🧸",
    "pet": "🐾", "travel": "✈️", "bank": "🏦", "card": "💳", "money": "💶", "salary": "💼",
    "shopping": "🛍️", "leisure": "🎬", "sport": "🏋️", "school": "🎓", "insurance": "🛡️",
    "tax": "🧾", "transport": "🚌", "coffee": "☕", "subscription": "🔁", "tools": "🛠️",
    "clothes": "👕", "beauty": "💇", "other": "📦", "solar": "☀️",
}


def icon(name: str | None) -> str:
    if not name:
        return ICONS["tag"]
    return ICONS.get(name, name)


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
env = templates.env
env.filters["eur"] = fmt_eur
env.filters["fdate"] = fmt_date
env.filters["icon"] = icon
env.filters["tojson_compact"] = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
env.globals.update(
    APP_NAME=get_settings().app_name,
    MONTH_NAMES=MONTH_NAMES,
    MONTH_SHORT=MONTH_SHORT,
    ICONS=ICONS,
    STATIC_VERSION="6",
)
