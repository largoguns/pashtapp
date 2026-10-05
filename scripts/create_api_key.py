#!/usr/bin/env python3
"""Crea una API key para /api/v1/quick-expense: python scripts/create_api_key.py "iPhone Atajos" """
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.services.auth import create_api_key  # noqa: E402

label = " ".join(sys.argv[1:]) or "CLI"
init_db()
with SessionLocal() as db:
    print(create_api_key(db, label))
