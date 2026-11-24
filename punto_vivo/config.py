"""Configuración del módulo PUNTO VIVO FRESH.

Todas las opciones se leen de variables de entorno con prefijo PV_.
Las credenciales nunca se envían al navegador.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

STATE_DIR = ROOT / "state"
UPLOAD_DIR = Path(__file__).resolve().parent / "static" / "uploads"
PROOF_DIR = STATE_DIR / "comprobantes"  # privado: nunca se sirve desde /static
DB_PATH = Path(os.getenv("PV_DB_PATH", str(STATE_DIR / "punto_vivo.sqlite3")))
SECRET_FILE = STATE_DIR / "punto_vivo_secret.key"

# Producción (Vercel/Render + Supabase). Si faltan estas variables se usa SQLite local.
SUPABASE_URL = os.getenv("PV_SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("PV_SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("PV_SUPABASE_BUCKET", "menu-images").strip() or "menu-images"
TRUST_PROXY = os.getenv("PV_TRUST_PROXY", "0").strip() == "1"

# Vercel monta el código en /var/task (solo lectura). Detectar el entorno
# serverless evita que el arranque intente crear directorios y tumbe la app.
IS_SERVERLESS = bool(
    os.getenv("VERCEL")
    or os.getenv("AWS_LAMBDA_FUNCTION_NAME")
)


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
MAX_PROOF_BYTES = int(os.getenv("PV_MAX_PROOF_BYTES", str(5 * 1024 * 1024)))
# Web Push: la clave pública puede enviarse al navegador; la privada nunca.
VAPID_PUBLIC_KEY = os.getenv("PV_VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("PV_VAPID_PRIVATE_KEY", "").strip()
VAPID_SUBJECT = os.getenv("PV_VAPID_SUBJECT", "https://punto-vivo-fresh.vercel.app").strip()
VAPID_FILE = STATE_DIR / "vapid.json"  # local, ignorado por git; en Vercel se usan variables
MAX_VISITAS_HORA_IP = int(os.getenv("PV_LIMITE_IP_HORA", "20"))
MAX_VISITAS_HORA_TEL = int(os.getenv("PV_LIMITE_TELEFONO_HORA", "6"))


def vapid_keys() -> tuple[str, str]:
    """Par (pública, privada): entorno primero, archivo local como respaldo."""
    publica, privada = VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY
    if not (publica and privada) and VAPID_FILE.exists():
        try:
            datos = json.loads(VAPID_FILE.read_text(encoding="utf-8"))
            publica = publica or str(datos.get("public", "")).strip()
            privada = privada or str(datos.get("private", "")).strip()
        except Exception:
            pass
    return publica, privada


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
    if supabase_enabled() or IS_SERVERLESS:
        # Vercel tiene /var/task en solo lectura. Crear 'state' aquí rompería
        # la importación de app.py y haría que /healthz devolviera 500.
        return
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Filesystem de solo lectura: la persistencia debe estar en Supabase.
        return
