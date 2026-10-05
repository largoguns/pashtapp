#!/usr/bin/env python3
"""Genera el valor de ADMIN_PASSWORD_HASH: python scripts/hash_password.py"""
import base64
import getpass

import bcrypt

pw = getpass.getpass("Contraseña: ")
if pw != getpass.getpass("Repite la contraseña: "):
    raise SystemExit("No coinciden.")
h = bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()
b64 = base64.b64encode(h.encode()).decode()
print("\nRecomendado (Portainer → Environment, o .env). Base64: sin '$', no le afecta la interpolación:")
print(f"ADMIN_PASSWORD_HASH={b64}")
print("\nAlternativa: hash en claro en .env (comillas simples obligatorias):")
print(f"ADMIN_PASSWORD_HASH='{h}'")
