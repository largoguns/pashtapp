"""Contraseñas (bcrypt), sesión en cookie firmada (JWT) y API keys."""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiKey, User

log = logging.getLogger("pashtapp.auth")

SESSION_COOKIE = "pashtapp_session"
_ALGO = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], password_hash.encode())
    except ValueError:
        return False


def create_session_token(username: str) -> str:
    settings = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    return jwt.encode({"sub": username, "exp": exp}, settings.secret_key, algorithm=_ALGO)


def decode_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGO])
    except JWTError:
        return None
    return payload.get("sub")


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user and verify_password(password, user.password_hash):
        return user
    # Igualar tiempos para no revelar si el usuario existe.
    if not user:
        verify_password(password, "$2b$12$" + "." * 53)
    return None


def ensure_admin(db: Session) -> None:
    """Crea/actualiza el usuario administrador a partir del entorno."""
    settings = get_settings()
    if not settings.admin_username:
        return
    pw_hash = settings.admin_password_hash
    if not pw_hash and settings.admin_password:
        if settings.is_production:
            log.warning("ADMIN_PASSWORD en claro en producción: usa ADMIN_PASSWORD_HASH.")
        pw_hash = hash_password(settings.admin_password)
    if not pw_hash:
        return
    user = db.scalar(select(User).where(User.username == settings.admin_username))
    if user is None:
        db.add(User(username=settings.admin_username, password_hash=pw_hash))
    elif settings.admin_password_hash and user.password_hash != pw_hash:
        user.password_hash = pw_hash
    db.commit()


# --- API keys ---------------------------------------------------------------
# Los tokens son aleatorios de alta entropía: SHA-256 basta y permite búsqueda directa.

def hash_api_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_key(db: Session, label: str) -> str:
    token = "pk_" + secrets.token_urlsafe(32)
    db.add(ApiKey(key_hash=hash_api_key(token), label=label))
    db.commit()
    return token


def verify_api_key(db: Session, token: str | None) -> ApiKey | None:
    if not token:
        return None
    return db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token.strip())))
