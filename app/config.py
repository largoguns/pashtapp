"""Configuración de PashtAPP leída de variables de entorno."""
from __future__ import annotations

import base64
import binascii
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _decode_password_hash(raw: str | None) -> str | None:
    """Acepta el hash bcrypt tal cual (``$2b$...``) o codificado en base64.

    El base64 no contiene ``$``, así que sobrevive a la interpolación de variables de
    Docker Compose / Portainer (``stack.env``), que corrompe los hashes bcrypt en claro.
    """
    if not raw:
        return None
    raw = raw.strip().strip("'\"")
    if raw.startswith("$2"):
        return raw
    try:
        decoded = base64.b64decode(raw, validate=True).decode("ascii").strip()
    except (binascii.Error, UnicodeDecodeError):
        decoded = ""
    if not decoded.startswith("$2"):
        raise RuntimeError(
            "ADMIN_PASSWORD_HASH no es un hash bcrypt ($2b$...) ni su versión base64. "
            "Genéralo con: python scripts/hash_password.py"
        )
    return decoded


@dataclass(frozen=True)
class Settings:
    app_name: str = "PashtAPP"
    app_env: str = "development"
    secret_key: str = ""
    database_url: str = "sqlite:///./data/pashtapp.db"
    admin_username: str | None = None
    admin_password_hash: str | None = None
    admin_password: str | None = None  # Sólo desarrollo: se hashea al arrancar.
    backup_dir: str = "./backups"
    session_hours: int = 24 * 30
    cookie_secure: bool = False
    timezone: str = "Europe/Madrid"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    secret = os.getenv("SECRET_KEY") or ""
    env = os.getenv("APP_ENV", "development")
    if not secret or secret.startswith("replace_with"):
        if env.lower() == "production":
            raise RuntimeError(
                "SECRET_KEY no configurada. Genera una con: "
                "python -c 'import secrets; print(secrets.token_urlsafe(64))'"
            )
        # En desarrollo se genera una por proceso (las sesiones no sobreviven reinicios).
        secret = secrets.token_urlsafe(64)
    return Settings(
        app_name=os.getenv("APP_NAME", "PashtAPP"),
        app_env=env,
        secret_key=secret,
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/pashtapp.db"),
        admin_username=os.getenv("ADMIN_USERNAME") or None,
        admin_password_hash=_decode_password_hash(os.getenv("ADMIN_PASSWORD_HASH")),
        admin_password=os.getenv("ADMIN_PASSWORD") or None,
        backup_dir=os.getenv("BACKUP_DIR", "./backups"),
        session_hours=int(os.getenv("SESSION_HOURS", str(24 * 30))),
        cookie_secure=_bool(os.getenv("COOKIE_SECURE"), False),
        timezone=os.getenv("TZ_NAME", "Europe/Madrid"),
    )
