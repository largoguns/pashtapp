"""Exportación CSV / Excel (Mejora 5)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import require_user
from app.services.exporter import full_backup_xlsx, transactions_csv
from app.utils import today

router = APIRouter(prefix="/export", dependencies=[Depends(require_user)])


@router.get("/transactions.csv")
def export_csv(year: int | None = None, month: int | None = None, db: Session = Depends(get_db)):
    suffix = f"{year or 'todo'}" + (f"_{month:02d}" if year and month else "")
    return Response(
        transactions_csv(db, year, month),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="pashtapp_movimientos_{suffix}.csv"'},
    )


@router.get("/full-backup.xlsx")
def export_xlsx(db: Session = Depends(get_db)):
    return Response(
        full_backup_xlsx(db),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="pashtapp_backup_{today():%Y%m%d}.xlsx"'},
    )
