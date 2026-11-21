"""Configuración del módulo PUNTO VIVO FRESH.

Todas las opciones se leen de variables de entorno con prefijo PV_.
Las credenciales nunca se envían al navegador.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

STATE_DIR = ROOT / "state"
UPLOAD_DIR = Path(__file__).resolve().parent / "static" / "uploads"
DB_PATH = Path(os.getenv("PV_DB_PATH", str(STATE_DIR / "punto_vivo.sqlite3")))
SECRET_FILE = STATE_DIR / "punto_vivo_secret.key"

# Producción (Vercel/Render + Supabase). Si faltan estas variables se usa SQLite local.
SUPABASE_URL = os.getenv("PV_SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("PV_SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("PV_SUPABASE_BUCKET", "menu-images").strip() or "menu-images"
TRUST_PROXY = os.getenv("PV_TRUST_PROXY", "0").strip() == "1"


def supabase_enabled() -> bool:
    """True solo cuando hay URL y clave de servicio (nunca se envían al navegador)."""
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)

# República Dominicana no aplica horario de verano: UTC-4 fijo.
TZ_DOMINICANA = timezone(timedelta(hours=-4), "America/Santo_Domingo")

WELCOME_PATH = "/bienvenida"
MENU_PATH = "/menu"
ADMIN_PATH = "/admin"
SESSION_HOURS = 8
VISITOR_COOKIE = "pv_visita"
ADMIN_COOKIE = "pv_admin"
CSRF_HEADER = "X-CSRF-Token"

# Vercel limita el cuerpo de petición a 4.5 MB; en producción se usa 4 MB.
MAX_IMAGE_BYTES = int(os.getenv("PV_MAX_IMAGE_BYTES", str(8 * 1024 * 1024)))
MAX_VISITAS_HORA_IP = int(os.getenv("PV_LIMITE_IP_HORA", "20"))
MAX_VISITAS_HORA_TEL = int(os.getenv("PV_LIMITE_TELEFONO_HORA", "6"))


def public_base_url(request=None) -> str:
    """Base pública estable del QR. Si no se configura, se deriva de la petición."""
    configured = os.getenv("PV_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    if request is not None:
        return str(request.base_url).rstrip("/")
    return "http://127.0.0.1:8790"


def welcome_url(request=None) -> str:
    """El QR SIEMPRE apunta aquí, nunca al menú."""
    return public_base_url(request) + WELCOME_PATH


def secret_key() -> str:
    """Secreto persistente para firmar cookies de visitante."""
    configured = os.getenv("PV_SECRET_KEY", "").strip()
    if configured:
        return configured
    if supabase_enabled():
        raise RuntimeError(
            "PV_SECRET_KEY es obligatorio en producción (Vercel/Render): el filesystem "
            "es de solo lectura. Genera un valor con "
            "python -c 'import secrets; print(secrets.token_hex(32))'"
        )
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            SECRET_FILE.chmod(0o600)
        except OSError:
            pass
    return SECRET_FILE.read_text(encoding="utf-8").strip()


def now_local() -> datetime:
    return datetime.now(TZ_DOMINICANA)


def ensure_dirs() -> None:
    """En producción Supabase guarda la base y las fotos: no hay disco que crear."""
    if supabase_enabled():
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
