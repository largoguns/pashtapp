#!/usr/bin/env python3
"""Genera el hash bcrypt para ADMIN_PASSWORD_HASH: python scripts/hash_password.py"""
import getpass

import bcrypt

pw = getpass.getpass("Contraseña: ")
if pw != getpass.getpass("Repite la contraseña: "):
    raise SystemExit("No coinciden.")
h = bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()
print("\nPara .env (comillas simples obligatorias):")
print(f"ADMIN_PASSWORD_HASH='{h}'")
print("\nPara el bloque environment de docker-compose.yml / Portainer ($ escapado):")
print(f"ADMIN_PASSWORD_HASH={h.replace('$', '$$')}")
