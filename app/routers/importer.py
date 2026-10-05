"""Importación del libro Excel desde la web: subir → vista previa → confirmar."""
from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import require_user
from app.services.backup import run_backup
from app.services.excel_import import ImportOptions, analyse, existing_counts, import_workbook, month_overview
from app.services.periods import current_period, get_start_day
from app.templating import templates
from app.utils import today

router = APIRouter(prefix="/import", dependencies=[Depends(require_user)])

MAX_BYTES = 15 * 1024 * 1024
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_MAX_AGE = 24 * 3600


def _upload_dir() -> Path:
    db_path = Path(get_settings().database_url.removeprefix("sqlite:///"))
    path = db_path.parent / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _upload_path(token: str) -> Path:
    if not _TOKEN.match(token):
        raise HTTPException(404, "Importación no encontrada")
    path = _upload_dir() / f"{token}.xlsx"
    if not path.exists():
        raise HTTPException(404, "El fichero subido ha caducado; vuelve a subirlo.")
    return path


def _cleanup_old() -> None:
    limit = time.time() - _MAX_AGE
    for f in _upload_dir().glob("*.xlsx"):
        if f.stat().st_mtime < limit:
            f.unlink(missing_ok=True)


def _guess_year(filename: str, db: Session) -> int:
    m = re.search(r"(20\d\d)", filename or "")
    return int(m.group(1)) if m else current_period(db)[0]


def _error(request: Request, message: str, status: int = 422):
    return templates.TemplateResponse(request, "import/upload.html", {"error": message}, status_code=status)


@router.get("")
def upload_form(request: Request):
    return templates.TemplateResponse(request, "import/upload.html", {"error": None})


@router.post("")
async def upload(request: Request, file: UploadFile = File(...), year: str | None = Form(None),
                 db: Session = Depends(get_db)):
    _cleanup_old()
    name = file.filename or ""
    if not name.lower().endswith((".xlsx", ".xlsm")):
        return _error(request, "El fichero debe ser un libro de Excel (.xlsx).")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return _error(request, "El fichero supera los 15 MB.")
    token = uuid.uuid4().hex
    path = _upload_dir() / f"{token}.xlsx"
    path.write_bytes(data)
    y = int(year) if year and year.strip().isdigit() else _guess_year(name, db)
    try:
        analyse(path, y, today())
    except Exception:  # openpyxl lanza tipos variados ante ficheros corruptos o no-xlsx
        path.unlink(missing_ok=True)
        return _error(request, "No se ha podido leer el fichero. ¿Es un .xlsx válido?")
    (path.with_suffix(".name")).write_text(name, encoding="utf-8")
    return RedirectResponse(f"/import/{token}?year={y}", status_code=303)


@router.get("/{token}")
def preview(request: Request, token: str, year: int, db: Session = Depends(get_db)):
    path = _upload_path(token)
    res = analyse(path, year, today())
    name_file = path.with_suffix(".name")
    return templates.TemplateResponse(request, "import/preview.html", {
        "token": token,
        "filename": name_file.read_text(encoding="utf-8") if name_file.exists() else path.name,
        "res": res,
        "overview": month_overview(res),
        "existing": existing_counts(db, year),
        "start_day": get_start_day(db) if get_start_day(db) > 1 else 27,
    })


@router.post("/{token}")
async def confirm(request: Request, token: str, db: Session = Depends(get_db)):
    path = _upload_path(token)
    form = await request.form()
    try:
        year = int(form.get("year", ""))
        start_day = max(1, min(28, int(form.get("start_day") or 27)))
    except ValueError:
        raise HTTPException(422, "Año o día de cobro no válidos")
    months = {int(m) for m in form.getlist("months") if str(m).isdigit()}
    if not months:
        raise HTTPException(422, "Selecciona al menos un mes")
    opts = ImportOptions(
        today=today(),
        months=months,
        on_existing="replace" if form.get("on_existing") == "replace" else "skip",
        loans=form.get("loans") == "true",
        replace_loans=form.get("replace_loans") == "true",
        utility=form.get("utility") == "true",
        templates=form.get("templates") == "true",
        start_day=start_day,
    )
    res = analyse(path, year, opts.today)
    backup = run_backup()  # red de seguridad: reemplazar meses borra sus movimientos
    report = import_workbook(db, res, opts)
    db.commit()
    path.unlink(missing_ok=True)
    path.with_suffix(".name").unlink(missing_ok=True)
    return templates.TemplateResponse(request, "import/result.html", {"report": report, "backup": backup.name})
