"""Seguridad: hash de contraseñas, sesiones, cookies firmadas y validación."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any

from . import repository
from .config import (
    SESSION_HOURS,
    secret_key,
)

PBKDF2_ROUNDS = 210_000


# ------------------------------------------------------------------ contraseñas

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------ sesiones admin

def create_session(admin_id: int) -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    now = time.time()
    repository.create_session(
        _sha256(token), admin_id, csrf, now, now + SESSION_HOURS * 3600
    )
    return token, csrf


def get_session(token: str | None) -> Any | None:
    if not token:
        return None
    row = repository.get_session_by_hash(_sha256(token))
    if row is None or float(row["expires_at"]) < time.time():
        if row is not None:
            repository.delete_session_by_hash(_sha256(token))
        return None
    return row


def delete_session(token: str | None) -> None:
    if token:
        repository.delete_session_by_hash(_sha256(token))


# ------------------------------------------------------------------ cookie visitante

def sign_visitor(payload: dict[str, Any]) -> str:
    data = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(secret_key().encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{data}.{signature}"


def verify_visitor(cookie: str | None) -> dict[str, Any] | None:
    if not cookie or "." not in cookie:
        return None
    data, signature = cookie.rsplit(".", 1)
    expected = hmac.new(secret_key().encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    except (ValueError, TypeError):
        return None
    if float(payload.get("exp", 0)) < time.time():
        return None
    return payload


# ------------------------------------------------------------------ validación

def validate_name(raw: str) -> str:
    name = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not (3 <= len(name) <= 100) or sum(ch.isalpha() for ch in name) < 3:
        raise ValueError("Escribe tu nombre completo (entre 3 y 100 caracteres).")
    if not all(ch.isalpha() or ch in " .'-" for ch in name):
        raise ValueError("El nombre solo puede contener letras, espacios, puntos, apóstrofos y guiones.")
    return name


def normalize_phone(raw: str) -> str:
    text = str(raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if digits.startswith("001"):
        digits = digits[3:]
    elif len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "78" or len(set(digits)) < 3:
        raise ValueError("Teléfono inválido. Usa 10 dígitos dominicanos, por ejemplo 809 000 0000.")
    return digits


def format_phone(phone: str) -> str:
    return f"({phone[:3]}) {phone[3:6]}-{phone[6:]}" if len(phone) == 10 else phone


def mask_phone(phone: str) -> str:
    return f"***-***-{phone[-4:]}" if len(phone) >= 4 else "***"


def ip_hash(ip: str) -> str:
    return hashlib.sha256(f"{ip}|{secret_key()}".encode("utf-8")).hexdigest()[:24]


# ------------------------------------------------------------------ límite de intentos

def allow_visit(client_hash: str, phone: str) -> bool:
    """Ventana deslizante de 1 hora por IP y por teléfono (anti-abuso)."""
    return repository.allow_visit(client_hash, _sha256(phone))


def login_bloqueado(client_hash: str) -> bool:
    """Solo cuenta fallos; un login correcto no consume intentos."""
    try:
        return repository.login_attempts(client_hash) >= repository.LOGIN_MAX_INTENTOS
    except Exception:
        return False


def registrar_fallo_login(client_hash: str) -> None:
    try:
        repository.register_login_failure(client_hash)
    except Exception:
        pass


def limpiar_intentos_login(client_hash: str) -> None:
    try:
        repository.clear_login_attempts(client_hash)
    except Exception:
        pass


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
