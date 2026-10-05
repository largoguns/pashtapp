"""Dependencias de FastAPI: sesión de BD y autenticación."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth import SESSION_COOKIE, decode_session_token, verify_api_key


class LoginRequired(Exception):
    """Se transforma en redirección a /login (o HX-Redirect en peticiones HTMX)."""


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    username = decode_session_token(request.cookies.get(SESSION_COOKIE))
    if not username:
        return None
    return db.scalar(select(User).where(User.username == username))


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise LoginRequired()
    return user


def require_api_access(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> str:
    """Acceso a /api/v1: cabecera ``X-API-Key`` o sesión web válida."""
    if x_api_key:
        key = verify_api_key(db, x_api_key)
        if key:
            return f"api:{key.label}"
        raise HTTPException(status_code=401, detail="API key inválida")
    user = current_user(request, db)
    if user:
        return f"user:{user.username}"
    raise HTTPException(status_code=401, detail="Se requiere X-API-Key o sesión iniciada")


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"
