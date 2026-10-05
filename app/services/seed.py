"""Categorías por defecto para una instalación vacía."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category

# Colores: paleta categórica validada para fondo oscuro, en orden fijo.
PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

DEFAULT_CATEGORIES = [
    # nombre, icono, fija por defecto
    ("Comida", "cart", False),
    ("Gasolina", "fuel", False),
    ("Hogar", "home", True),
    ("Luz", "bolt", True),
    ("Restaurantes", "food", False),
    ("Compras", "shopping", False),
    ("Salud", "health", False),
    ("Ocio", "leisure", False),
    ("Suscripciones", "subscription", True),
    ("Transporte", "transport", False),
    ("Préstamos", "bank", True),
    ("Nómina", "salary", True),
    ("Otros", "other", False),
]


def seed_defaults(db: Session) -> None:
    if db.scalar(select(func.count(Category.id))):
        return
    for i, (name, icon, fixed) in enumerate(DEFAULT_CATEGORIES):
        db.add(Category(name=name, icon=icon, color_hex=PALETTE[i % len(PALETTE)], is_fixed_default=fixed))
    db.commit()
