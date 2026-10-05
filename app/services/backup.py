"""Copias de seguridad bajo demanda (el contenedor ``pashtapp_backup`` hace las diarias)."""
from __future__ import annotations

import gzip
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import get_settings

BACKUP_GLOB = "pashtapp_*.db.gz"


def _db_path() -> Path:
    url = get_settings().database_url
    return Path(url.removeprefix("sqlite:///"))


@dataclass
class BackupFile:
    name: str
    size_kb: float
    modified: datetime


def run_backup() -> Path:
    """Backup online con la API de SQLite (seguro con WAL) y compresión gzip."""
    backup_dir = Path(get_settings().backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo(get_settings().timezone)).strftime("%Y%m%d_%H%M%S")
    raw = backup_dir / f"pashtapp_{stamp}.db"
    src = sqlite3.connect(_db_path())
    dst = sqlite3.connect(raw)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    gz = raw.with_suffix(".db.gz")
    with raw.open("rb") as fin, gzip.open(gz, "wb") as fout:
        shutil.copyfileobj(fin, fout)
    raw.unlink()
    return gz


def list_backups(limit: int = 15) -> list[BackupFile]:
    backup_dir = Path(get_settings().backup_dir)
    if not backup_dir.is_dir():
        return []
    files = sorted(backup_dir.glob(BACKUP_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        BackupFile(p.name, round(p.stat().st_size / 1024, 1), datetime.fromtimestamp(p.stat().st_mtime))
        for p in files[:limit]
    ]
