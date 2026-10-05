"""Parser de SMS / notificaciones bancarias (Mejora 2)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.utils import parse_amount

# Regex de la especificación, ampliada para aceptar separador de miles ("1.234,56 EUR").
AMOUNT_RE = re.compile(
    r"(?i)(?:importe|pago|compra|por)?\s*([0-9]{1,3}(?:[.,\s][0-9]{3})*[.,][0-9]{2}|[0-9]+[.,][0-9]{2})\s*(?:EUR|€)"
)

# Palabra clave (regex) -> categoría. El primer acierto gana.
CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    (r"repsol|cepsa|galp|bp\b|shell|petronor|ballenoil|plenoil|gasolin|gasoil|diesel|glp", "Gasolina"),
    (r"mercadona|carrefour|lidl|aldi|dia\b|eroski|alcampo|hipercor|consum|ahorramas|supercor", "Comida"),
    (r"amazon|aliexpress|zara|primark|decathlon|ikea|leroy", "Compras"),
    (r"farmacia|clinica|clínica|hospital|dentista", "Salud"),
    (r"restaurante|bar\b|cafeter|burger|mcdonald|telepizza|glovo|just ?eat|uber ?eats", "Restaurantes"),
    (r"renfe|metro|emt|cabify|uber|bolt|parking|aparcamiento|peaje", "Transporte"),
    (r"netflix|spotify|hbo|disney|prime video|movistar|vodafone|orange|digi", "Suscripciones"),
]
DEFAULT_CATEGORY = "Otros"

_MERCHANT_RE = re.compile(r"(?i)\b(?:en|comercio:?)\s+([A-Z0-9ÁÉÍÓÚÑ][\w .&'*\-]{1,40}?)(?=[.,;]\s|\s+(?:el|con|tarjeta|saldo|fecha|a las)\b|$|\.{2,}|\.$)")


@dataclass
class ParsedExpense:
    amount: float | None
    concept: str
    category: str


def guess_category(text: str) -> str:
    for pattern, category in CATEGORY_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return DEFAULT_CATEGORY


def parse_amount_from_text(text: str) -> float | None:
    m = AMOUNT_RE.search(text)
    if not m:
        return None
    return parse_amount(m.group(1).replace(" ", ""))


def extract_merchant(text: str) -> str | None:
    m = _MERCHANT_RE.search(text)
    if not m:
        return None
    merchant = m.group(1).strip(" .,-*")
    return merchant.title() if merchant.isupper() else merchant


def parse_bank_text(text: str) -> ParsedExpense:
    text = text.strip()
    merchant = extract_merchant(text)
    return ParsedExpense(
        amount=parse_amount_from_text(text),
        concept=merchant or text[:60],
        category=guess_category(text),
    )
