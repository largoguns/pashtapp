"""Importación del extracto CSV de la cuenta de ahorro (Revolut «Cuenta Remunerada»).

Formato (exportación en español de Revolut):
    Fecha de transacción/valor,Descripción,TAE,TIN,Dinero entrante,Dinero saliente,Saldo
    5 sept 2026,Depósito en «Cuenta Remunerada»,,,"1.500,00€",,"1.500,00€"
También acepta la exportación en inglés (Date, Description, Money in, Money out, Balance).

Cada línea se guarda una sola vez (huella: fecha, importe, saldo y descripción), así que se puede
reimportar el extracto de cada mes. Los depósitos y retiradas se emparejan con los traspasos de la
cuenta principal (mismo importe, fecha cercana); lo que no case queda pendiente de revisión.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import SavingsAccount, SavingsMovement, SavingsStatementLine, Transaction
from app.utils import parse_amount

MONTHS = {"ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "ago": 8, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12}
MATCH_WINDOW = timedelta(days=7)  # el dinero pasa por la cuenta corriente de Revolut


class StatementError(ValueError):
    pass


@dataclass
class ParsedLine:
    date: date
    description: str
    amount: float
    balance: float

    @property
    def kind(self) -> str:
        if "inter" in _norm(self.description):
            return "interest"
        return "deposit" if self.amount > 0 else "withdrawal"

    @property
    def fingerprint(self) -> str:
        raw = f"{self.date.isoformat()}|{self.amount:.2f}|{self.balance:.2f}|{_norm(self.description)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _decode(data: bytes) -> str:
    text = data.decode("utf-8-sig", errors="replace")
    if "Ã" in text or "â\x82¬" in text:  # UTF-8 leído como Latin-1 en algún paso: deshacer
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text.replace(" ", " ").replace(" ", " ")


def _date(raw: str) -> date:
    m = re.match(r"\s*(\d{1,2})\s+([A-Za-zé.]+)\s+(\d{4})", raw or "")
    if m:
        month = MONTHS.get(_norm(m.group(2)).rstrip(".")[:4]) or MONTHS.get(_norm(m.group(2))[:3])
        if month:
            return date(int(m.group(3)), month, int(m.group(1)))
    m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})", raw or "")
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", raw or "")
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    raise StatementError(f"Fecha no reconocida: {raw!r}")


def parse_statement(data: bytes) -> list[ParsedLine]:
    rows = list(csv.reader(io.StringIO(_decode(data))))
    if not rows:
        raise StatementError("El fichero está vacío")
    head = [_norm(h) for h in rows[0]]

    def col(*keys):
        return next((i for i, h in enumerate(head) if any(k in h for k in keys)), None)

    c_date, c_desc = col("fecha", "date"), col("descrip", "description")
    c_in, c_out, c_bal = col("entrante", "money in", "paid in"), col("saliente", "money out", "paid out"), \
        col("saldo", "balance")
    if None in (c_date, c_desc, c_bal) or (c_in is None and c_out is None):
        raise StatementError("No parece un extracto de Revolut: faltan columnas de fecha, importes o saldo")
    lines = []
    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        get = lambda i: row[i] if i is not None and i < len(row) else ""  # noqa: E731
        money_in, money_out = parse_amount(get(c_in)) or 0.0, parse_amount(get(c_out)) or 0.0
        balance = parse_amount(get(c_bal))
        if balance is None or (not money_in and not money_out):
            continue
        lines.append(ParsedLine(_date(get(c_date)), get(c_desc).strip(), round(money_in - abs(money_out), 2),
                                round(balance, 2)))
    if not lines:
        raise StatementError("El extracto no tiene movimientos")
    return lines


@dataclass
class StatementReport:
    new: int = 0
    duplicates: int = 0
    matched: int = 0
    pending: int = 0
    interest: float = 0.0
    first: date | None = None
    last: date | None = None
    last_balance: float | None = None
    warnings: list[str] = field(default_factory=list)


def import_statement(db: Session, account: SavingsAccount, lines: list[ParsedLine]) -> StatementReport:
    report = StatementReport()
    existing = set(db.scalars(select(SavingsStatementLine.fingerprint).where(
        SavingsStatementLine.account_id == account.id)))
    had_lines = bool(existing)
    # Continuidad del saldo (detecta filas perdidas o un fichero manipulado).
    for prev, cur in zip(lines, lines[1:]):
        if abs(prev.balance + cur.amount - cur.balance) > 0.011:
            report.warnings.append(f"Salto de saldo el {cur.date:%d/%m/%Y}: {prev.balance:.2f} + {cur.amount:.2f} ≠ {cur.balance:.2f}")
    new_rows = []
    for ln in lines:
        if ln.fingerprint in existing:
            report.duplicates += 1
            continue
        existing.add(ln.fingerprint)
        row = SavingsStatementLine(account_id=account.id, date=ln.date, description=ln.description, amount=ln.amount,
                                   balance=ln.balance, kind=ln.kind, fingerprint=ln.fingerprint)
        db.add(row)
        new_rows.append(row)
        report.new += 1
        if ln.kind == "interest":
            report.interest += ln.amount
    db.flush()
    report.interest = round(report.interest, 2)
    report.first, report.last, report.last_balance = lines[0].date, lines[-1].date, lines[-1].balance

    # Primera importación: la cuenta empieza donde empieza el extracto.
    if not had_lines:
        first = lines[0]
        account.opening_date = first.date
        account.opening_balance = round(first.balance - first.amount, 2)

    for row in new_rows:
        if row.kind == "interest":
            continue
        if auto_match(db, account, row):
            report.matched += 1
        else:
            report.pending += 1
    return report


def _taken_tx_ids(db: Session, account: SavingsAccount) -> set[int]:
    return set(db.scalars(select(SavingsStatementLine.transaction_id).where(
        SavingsStatementLine.account_id == account.id, SavingsStatementLine.transaction_id.is_not(None))))


def match_candidates(db: Session, account: SavingsAccount, line: SavingsStatementLine,
                     window: timedelta = MATCH_WINDOW, exact: bool = True) -> list[Transaction]:
    """Movimientos de la cuenta principal que pueden ser la otra cara de la línea."""
    taken = _taken_tx_ids(db, account) - {line.transaction_id}
    q = select(Transaction).where(Transaction.date.between(line.date - window, line.date + window))
    if line.kind == "deposit":
        q = q.where(Transaction.is_income.is_(False))
    else:
        q = q.where(Transaction.is_income.is_(True))
    if exact:
        q = q.where(func_abs(Transaction.amount) == abs(line.amount))
    else:
        # Sin importe exacto sólo tiene sentido proponer traspasos ya vinculados a esta cuenta.
        q = q.where(Transaction.savings_account_id == account.id)
    txs = [t for t in db.scalars(q).unique() if t.id not in taken]
    # Primero los vinculados a esta cuenta y los más cercanos en fecha.
    txs.sort(key=lambda t: (t.savings_account_id != account.id, abs((t.booking_date - line.date).days)))
    return txs


def func_abs(col):
    return func.round(func.abs(col), 2)


def auto_match(db: Session, account: SavingsAccount, line: SavingsStatementLine) -> bool:
    linked = [t for t in match_candidates(db, account, line) if t.savings_account_id == account.id]
    if linked:
        line.transaction_id = linked[0].id
        line.classification = "caixabank"
        return True
    if line.kind == "withdrawal":
        # ¿Ya apuntado a mano como «gasto pagado con la cuenta»?
        mv = db.scalar(select(SavingsMovement).where(
            SavingsMovement.account_id == account.id, SavingsMovement.kind == "expense",
            func_abs(SavingsMovement.amount) == abs(line.amount),
            SavingsMovement.date.between(line.date - MATCH_WINDOW, line.date + MATCH_WINDOW)))
        if mv:
            line.classification, line.label, line.category_id = "expense", mv.name, mv.category_id
            return True
    return False


def classify(db: Session, account: SavingsAccount, line: SavingsStatementLine, classification: str,
             *, transaction_id: int | None = None, label: str | None = None, category_id: int | None = None) -> None:
    allowed = {"deposit": {"caixabank", "external"}, "withdrawal": {"caixabank", "expense", "lent"}}[line.kind]
    if classification not in allowed:
        raise ValueError(f"Clasificación no válida para {line.kind}: {classification}")
    line.classification = classification
    line.label = (label or "").strip() or None
    line.category_id = category_id if classification == "expense" else None
    line.transaction_id = None
    if classification == "caixabank" and transaction_id:
        tx = db.get(Transaction, transaction_id)
        if tx is None:
            raise ValueError("Movimiento de la cuenta principal no encontrado")
        line.transaction_id = tx.id
        tx.savings_account_id = account.id  # queda como traspaso vinculado
