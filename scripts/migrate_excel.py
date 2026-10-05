#!/usr/bin/env python3
"""Migración inicial desde el libro «Pashta Saeta» (.xlsx) a PashtAPP (Mejora 1).

Uso:
    python scripts/migrate_excel.py "Pashta Saeta 2026.xlsx" --dry-run   # analizar sin escribir
    python scripts/migrate_excel.py "Pashta Saeta 2026.xlsx"             # importar (BD vacía)
    python scripts/migrate_excel.py "Pashta Saeta 2026.xlsx" --reset     # vaciar datos e importar
    python scripts/migrate_excel.py "Pashta Saeta 2026.xlsx" --inspect   # volcar celdas de cada hoja

Estructura esperada del libro (anclada en etiquetas, no en filas fijas):
  * Hojas «Enero»…«Diciembre»:
      - Bloque ENTRADA: «Salario» (D) → ingreso; «Resto mes anterior» (D) de enero → saldo inicial.
      - Bloque «GASTOS FIJOS …»: B casilla cobrado, C concepto, D importe.
      - Bloque «GASTOS»: B casilla, C categoría, D descripción, F importe (negativo = devolución).
  * «Gastos por Categoría»: columna A con las categorías (más la lista de validación de las hojas).
  * «Gastos Electricos»: A mes, B importe (fórmula a la fila «Electricidad» de cada mes).
  * «Coche», «Grueso», «Placas»: Nº de Cuota, Vencimiento, Cuota, Amortización, Intereses, Pendiente.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]
LOAN_SHEETS = ("Coche", "Grueso", "Placas")
CATEGORY_SHEET = "gastos por categoria"
UTILITY_SHEET = "gastos electricos"
NOTE = "[migrado Excel]"

# Categoría de cada gasto fijo del libro (el resto cae en «Fijos»).
FIXED_CATEGORY = {
    "hipoteca": "Vivienda", "seguro hogar": "Vivienda", "alarma": "Vivienda", "agua": "Vivienda",
    "telefono": "Suministros", "glp": "Suministros", "electricidad": "Luz",
    "gasolina": "Gasolina", "comida": "Comida",
    "seguro vida": "Seguros", "seguro coche": "Seguros",
    "ninos": "Familia", "extra aly": "Familia", "extra papa": "Familia", "impuestos": "Impuestos", "ahorro": "Ahorro",
}
CATEGORY_ICON = {
    "comida": "🛒", "ocio": "🎬", "trabajo": "💼", "farmacia": "💊", "hogar": "🏠", "regalos": "🎁",
    "ana": "🎓", "estanco": "🚬", "comida externa": "🍽️", "reintegros": "🏧", "vacaciones": "✈️",
    "devoluciones": "↩️", "fiestas": "🎉", "salario": "💶", "ahorro": "🐷", "vivienda": "🏡",
    "suministros": "🔌", "luz": "⚡", "gasolina": "⛽", "seguros": "🛡️", "familia": "🧸",
    "impuestos": "🧾", "prestamos": "🏦", "fijos": "📌", "otros": "📦",
}
FIXED_CATEGORIES = {"vivienda", "suministros", "luz", "seguros", "familia", "impuestos", "prestamos", "fijos", "salario"}
PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]


def norm(v) -> str:
    s = unicodedata.normalize("NFKD", str(v or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from app.utils import parse_amount

    return parse_amount(str(v)) if re.search(r"\d", str(v)) else None


def as_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


# ----------------------------------------------------------------------------- estructuras

@dataclass
class Tx:
    year: int
    month: int
    kind: str              # income | fixed | variable
    name: str
    amount: float          # con signo: + ingreso / - gasto (devolución: + no ingreso)
    settled: bool
    category: str
    notes: str = NOTE


@dataclass
class LoanData:
    name: str
    rows: list[dict]
    capital: float
    rate: float
    term: int
    fee: float
    start: date


@dataclass
class Result:
    year: int
    categories: list[str] = field(default_factory=list)
    transactions: list[Tx] = field(default_factory=list)
    loans: list[LoanData] = field(default_factory=list)
    utility: dict[int, float] = field(default_factory=dict)
    opening: float | None = None
    carry: dict[int, float] = field(default_factory=dict)  # «Resto mes anterior» de cada mes (validación)
    months: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def find_label(ws, pattern: str, col: int | None = None) -> int | None:
    """Fila (1-based) de la primera celda cuyo texto normalizado casa con ``pattern``."""
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and (col is None or c.column == col) and re.search(pattern, norm(c.value)):
                return c.row
    return None


# ----------------------------------------------------------------------------- hojas

def parse_month(ws, year: int, month: int, today: date, res: Result) -> None:
    v = lambda r, c: ws.cell(r, c).value  # noqa: E731
    is_past_or_current = (year, month) <= (today.year, today.month)

    r_salary = find_label(ws, r"^salario", col=3)
    r_carry = find_label(ws, r"^resto mes anterior", col=3)
    r_fixed = find_label(ws, r"^gastos fijos", col=3)
    r_var = find_label(ws, r"^gastos$", col=3)
    if not (r_fixed and r_var):
        res.warnings.append(f"[{ws.title}] no se encuentran los bloques GASTOS FIJOS / GASTOS")
        return

    if r_salary and num(v(r_salary, 4)):
        # El salario no tiene casilla: se da por cobrado en meses pasados y en el actual.
        res.transactions.append(Tx(year, month, "income", "Salario", abs(num(v(r_salary, 4))),
                                   is_past_or_current, "Salario"))
    if r_carry and num(v(r_carry, 4)) is not None:
        res.carry[month] = num(v(r_carry, 4))

    for r in range(r_fixed + 1, r_var):
        name, amount = v(r, 3), num(v(r, 4))
        if not isinstance(name, str) or not name.strip() or not amount:
            continue
        name = name.strip()
        res.transactions.append(Tx(year, month, "fixed", name, -amount, v(r, 2) is True,
                                   FIXED_CATEGORY.get(norm(name), "Fijos")))

    empty = 0
    for r in range(r_var + 1, ws.max_row + 1):
        cat, desc, amount = v(r, 3), v(r, 4), num(v(r, 6))
        if amount in (None, 0):
            empty = empty + 1 if cat is None and desc is None else 0
            if empty > 40:
                break
            continue
        empty = 0
        cat = str(cat).strip() if cat else None
        desc = str(desc).strip() if desc else None
        if not cat:
            from app.services.parser import guess_category

            cat = guess_category(desc or "")
        res.transactions.append(Tx(year, month, "variable", desc or cat, -amount, v(r, 2) is True, cat))
    res.months.append(month)


def parse_categories(wb, res: Result) -> None:
    names: list[str] = []
    for ws in wb.worksheets:
        if norm(ws.title) == CATEGORY_SHEET:
            for (val,) in ws.iter_rows(min_row=2, max_col=1, values_only=True):
                if isinstance(val, str) and val.strip():
                    names.append(val.strip())
    # Lista desplegable (validación de datos) de la columna de categoría de los meses.
    for ws in wb.worksheets:
        if norm(ws.title) in MONTHS and ws.data_validations:
            for dv in ws.data_validations.dataValidation:
                if dv.type == "list" and dv.formula1 and dv.formula1.startswith('"'):
                    names.extend(x.strip() for x in dv.formula1.strip('"').split(",") if x.strip())
    seen = set()
    res.categories = [n for n in names if not (norm(n) in seen or seen.add(norm(n)))]


def parse_utility(wb, res: Result, settled_electricity: dict[int, bool]) -> None:
    ws = next((w for w in wb.worksheets if norm(w.title) == UTILITY_SHEET), None)
    if ws is None:
        res.warnings.append("No hay hoja «Gastos Electricos»")
        return
    for name, amount in ws.iter_rows(min_col=1, max_col=2, values_only=True):
        if norm(name) in MONTHS and num(amount):
            m = MONTHS.index(norm(name)) + 1
            # Los meses sin el cargo «Electricidad» conciliado son estimaciones del libro: se omiten.
            if settled_electricity.get(m, False):
                res.utility[m] = round(num(amount), 2)


def parse_loan(ws, res: Result) -> None:
    from app.utils import add_months

    header = None
    for row in ws.iter_rows(max_row=10):
        heads = [norm(c.value) for c in row]
        if any("interes" in h for h in heads) and any("pendiente" in h for h in heads):
            header = row[0].row
            cols = {
                "number": next(i for i, h in enumerate(heads) if h.startswith("n")),
                "due": next(i for i, h in enumerate(heads) if "vencim" in h or "fecha" in h),
                "payment": next(i for i, h in enumerate(heads) if "cuota" in h and not h.startswith("n")),
                "capital": next(i for i, h in enumerate(heads) if "amortiz" in h),
                "interest": next(i for i, h in enumerate(heads) if "interes" in h),
                "remaining": next(i for i, h in enumerate(heads) if "pendiente" in h),
            }
            break
    if header is None:
        res.warnings.append(f"[{ws.title}] sin cuadro de amortización reconocible")
        return
    rows = []
    for vals in ws.iter_rows(min_row=header + 1, values_only=True):
        payment, due = num(vals[cols["payment"]]), as_date(vals[cols["due"]])
        if payment is None or due is None:
            continue
        rows.append({
            "number": int(num(vals[cols["number"]]) or len(rows) + 1), "due": due, "payment": payment,
            "capital": num(vals[cols["capital"]]) or 0.0, "interest": num(vals[cols["interest"]]) or 0.0,
            "remaining": num(vals[cols["remaining"]]) or 0.0,
        })
    if not rows:
        res.warnings.append(f"[{ws.title}] cuadro vacío")
        return
    # TIN: mediana de interés / capital vivo de las primeras cuotas (la 1ª puede ser un periodo parcial).
    rates, prev = [], rows[0]["remaining"] + rows[0]["capital"]
    for r in rows[:12]:
        if prev > 0 and r["interest"] > 0:
            rates.append(r["interest"] / prev * 1200)
        prev = r["remaining"]
    res.loans.append(LoanData(
        name=ws.title.strip(),
        rows=rows,
        capital=round(rows[0]["remaining"] + rows[0]["capital"], 2),
        rate=round(median(rates), 3) if rates else 0.0,
        term=len(rows),
        fee=Counter(round(r["payment"], 2) for r in rows).most_common(1)[0][0],
        start=add_months(rows[0]["due"], -1),
    ))


def analyse(path: Path, year: int, today: date) -> Result:
    wb = load_workbook(path, data_only=True)  # valores calculados de las fórmulas
    res = Result(year=year)
    parse_categories(wb, res)
    for ws in wb.worksheets:
        if norm(ws.title) in MONTHS:
            parse_month(ws, year, MONTHS.index(norm(ws.title)) + 1, today, res)
        elif ws.title.strip() in LOAN_SHEETS:
            parse_loan(ws, res)
    elec = {t.month: t.settled for t in res.transactions if t.kind == "fixed" and norm(t.name) == "electricidad"}
    parse_utility(wb, res, elec)
    if 1 in res.carry:
        res.opening = res.carry[1]
    return res


def is_loan_payment(name: str, loans: list[LoanData]) -> LoanData | None:
    """«Coche», «Préstamo Grueso»… (pero no «Seguro Coche»)."""
    n = norm(name)
    for loan in loans:
        if re.fullmatch(rf"(prestamo\s+)?{re.escape(norm(loan.name))}", n):
            return loan
    return None


# ----------------------------------------------------------------------------- informe

def summary(res: Result) -> None:
    kinds = Counter(t.kind for t in res.transactions)
    print(f"Año {res.year} · meses {res.months}")
    print(f"Categorías: {', '.join(res.categories)}")
    print(f"Movimientos: {len(res.transactions)} (ingresos {kinds['income']}, fijos {kinds['fixed']}, "
          f"variables {kinds['variable']}; conciliados {sum(t.settled for t in res.transactions)})")
    for ln in res.loans:
        print(f"Préstamo {ln.name}: {ln.capital:.2f} € · {ln.rate} % TIN · {ln.term} cuotas de {ln.fee:.2f} € "
              f"· {ln.rows[0]['due']} → {ln.rows[-1]['due']}")
    print(f"Luz: {len(res.utility)} meses · saldo inicial (Resto mes anterior de enero): {res.opening}")
    for w in res.warnings:
        print("AVISO:", w)


def inspect(path: Path, max_rows: int = 40) -> None:
    wb = load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        print(f"\n=== «{ws.title}» ({ws.max_row}×{ws.max_column}) ===")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                break
            cells = [f"{chr(65 + c)}:{v!r}" for c, v in enumerate(row) if v is not None]
            if cells:
                print(f"{i + 1:>4} │ " + "  ".join(cells)[:240])


# ----------------------------------------------------------------------------- escritura

def write(res: Result, *, reset: bool, templates: bool, today: date) -> dict:
    from sqlalchemy import delete, func, select

    from app.database import SessionLocal, init_db
    from app.models import (Category, Loan, LoanInstallment, MonthOpening, RecurringTemplate, Transaction,
                            UtilityReading)
    from app.services.balances import initial_balance
    from app.services.months import mark_opened
    from app.services.settings_store import set_opening_balance
    from app.utils import clamp_day

    init_db()
    with SessionLocal() as db:
        if reset:
            for model in (Transaction, LoanInstallment, Loan, RecurringTemplate, UtilityReading, MonthOpening, Category):
                db.execute(delete(model))
            db.commit()
        elif db.scalar(select(func.count(Transaction.id))):
            sys.exit("La base de datos ya tiene movimientos. Usa --reset para vaciarla antes de importar.")

        cats: dict[str, Category] = {norm(c.name): c for c in db.scalars(select(Category))}

        def category(name: str) -> Category:
            key = norm(name)
            if key not in cats:
                cats[key] = Category(
                    name=name, icon=CATEGORY_ICON.get(key, "tag"), color_hex=PALETTE[len(cats) % len(PALETTE)],
                    is_fixed_default=key in FIXED_CATEGORIES,
                )
                db.add(cats[key])
                db.flush()
            return cats[key]

        for name in res.categories:
            category(name)

        # Préstamos con su cuadro.
        installments: dict[tuple[str, int, int], LoanInstallment] = {}
        for ln in res.loans:
            loan = Loan(name=ln.name, initial_capital=ln.capital, annual_interest_rate=ln.rate,
                        term_months=ln.term, start_date=ln.start, monthly_fee=ln.fee, active=True)
            for r in ln.rows:
                inst = LoanInstallment(
                    installment_number=r["number"], due_date=r["due"], payment_amount=r["payment"],
                    capital_amount=r["capital"], interest_amount=r["interest"], remaining_capital=r["remaining"],
                    is_paid=r["due"] <= today,
                )
                loan.installments.append(inst)
                installments[(norm(ln.name), r["due"].year, r["due"].month)] = inst
            loan.active = any(not i.is_paid for i in loan.installments)
            db.add(loan)
        db.flush()

        loan_cat = category("Préstamos")
        loans_due_day = {norm(ln.name): ln.rows[0]["due"].day for ln in res.loans}
        for t in res.transactions:
            loan = is_loan_payment(t.name, res.loans) if t.kind == "fixed" else None
            day = loans_due_day.get(norm(loan.name), 1) if loan else 1
            d = clamp_day(t.year, t.month, day)
            tx = Transaction(
                date=d, settlement_date=d, name=t.name, amount=round(t.amount, 2), is_income=t.kind == "income",
                category_id=(loan_cat if loan else category(t.category)).id, is_fixed=t.kind != "variable",
                is_settled=t.settled, notes=t.notes,
            )
            if loan:
                inst = installments.get((norm(loan.name), t.year, t.month))
                if inst:
                    tx.loan_installment_id = inst.id
                    tx.notes = f"[Cuota {inst.installment_number}/{len(loan.rows)}] {NOTE}"
                    inst.is_paid = t.settled  # el libro manda en los meses importados
            db.add(tx)

        for m, amount in res.utility.items():
            db.add(UtilityReading(year=res.year, month=m, amount=amount, notes=NOTE))

        for m in res.months:
            mark_opened(db, res.year, m)  # sus fijos ya están importados: no regenerar

        set_opening_balance(db, res.opening or 0.0, res.year, min(res.months) if res.months else 1)

        # Plantillas de fijos para los meses siguientes, a partir del último mes importado.
        if templates and res.months and not db.scalar(select(func.count(RecurringTemplate.id))):
            last = max(res.months)
            for t in res.transactions:
                if t.month != last or t.kind == "variable" or is_loan_payment(t.name, res.loans):
                    continue
                db.add(RecurringTemplate(name=t.name, default_amount=abs(t.amount), category_id=category(t.category).id,
                                         day_of_month=1, is_income=t.kind == "income", active=True))
        db.commit()

        # Validación: el arrastre calculado frente al «Resto mes anterior» del libro.
        checks = {}
        for m in sorted(res.carry):
            if m == min(res.months):
                continue
            checks[m] = (initial_balance(db, res.year, m), res.carry[m])
        return checks


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xlsx", type=Path, nargs="?", default=Path("Pashta Saeta 2026.xlsx"))
    ap.add_argument("--year", type=int, help="Año de las hojas mensuales (por defecto, el del nombre del fichero)")
    ap.add_argument("--db", help="DATABASE_URL de destino (por defecto, la del entorno)")
    ap.add_argument("--today", type=date.fromisoformat, help="Fecha de referencia (AAAA-MM-DD) para cuotas pagadas")
    ap.add_argument("--inspect", action="store_true", help="Vuelca las primeras filas de cada hoja y sale")
    ap.add_argument("--dry-run", action="store_true", help="Analiza y muestra el resumen sin escribir")
    ap.add_argument("--reset", action="store_true", help="Vacía categorías, movimientos, préstamos, plantillas y luz")
    ap.add_argument("--no-templates", action="store_true", help="No crear plantillas de fijos")
    args = ap.parse_args()

    if not args.xlsx.exists():
        sys.exit(f"No existe el fichero: {args.xlsx}")
    if args.inspect:
        inspect(args.xlsx)
        return
    if args.db:
        os.environ["DATABASE_URL"] = args.db
    from app.utils import today as today_fn

    today = args.today or today_fn()
    year = args.year or int((re.search(r"(20\d\d)", args.xlsx.name) or [None, today.year])[1])

    res = analyse(args.xlsx, year, today)
    summary(res)
    if args.dry_run:
        return
    checks = write(res, reset=args.reset, templates=not args.no_templates, today=today)
    print("✔ Migración completada.")
    if checks:
        print("\nValidación del arrastre de saldo (PashtAPP vs «Resto mes anterior» del libro):")
        for m, (ours, theirs) in checks.items():
            flag = "✓" if abs(ours - theirs) < 0.01 else f"Δ {ours - theirs:+.2f}"
            print(f"  {MONTHS[m - 1]:<11} {ours:>10.2f} {theirs:>10.2f}  {flag}")


if __name__ == "__main__":
    main()
