"""Migración con el libro real, si está disponible en la raíz del repo (no se versiona)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "Pashta Saeta 2026.xlsx"


@pytest.mark.skipif(not BOOK.exists(), reason="Libro original no disponible")
def test_migration_matches_workbook_balances(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'm.db'}"
    out = subprocess.run(
        [sys.executable, "scripts/migrate_excel.py", str(BOOK), "--db", db_url, "--today", "2026-10-05"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    assert "Migración completada" in out
    for month in ("febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre"):
        line = next(l for l in out.splitlines() if l.strip().startswith(month))
        assert line.rstrip().endswith("✓"), line
