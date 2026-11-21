"""Registro atómico de clientes y visitas (una sola transacción).

La validación y los límites siguen aquí; la escritura la hace ``repository``,
que usa SQLite en local y el RPC de Supabase en producción.
"""
from __future__ import annotations

import re
import time
from typing import Any

from . import repository, security
from .config import now_local

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
DUPLICATE_WINDOW_SECONDS = 20


class VisitError(Exception):
    """Error de validación o límite, seguro para mostrar al usuario."""


def register_visit(full_name: str, phone: str, request_token: str, client_ip: str) -> dict[str, Any]:
    """Crea/actualiza el cliente y añade una visita de forma atómica.

    - El cliente se identifica por teléfono normalizado (no se duplica).
    - ``request_token`` garantiza idempotencia ante doble envío.
    - Una ventana corta de 20 s evita visitas accidentales repetidas.
    """
    if not TOKEN_RE.match(str(request_token or "")):
        raise VisitError("Solicitud inválida. Recarga la página e inténtalo de nuevo.")

    # Doble validación en servidor, incluso si la capa HTTP se omitiese.
    try:
        full_name = security.validate_name(full_name)
        phone = security.normalize_phone(phone)
    except ValueError as exc:
        raise VisitError(str(exc)) from exc

    client_hash = security.ip_hash(client_ip)
    if not security.allow_visit(client_hash, phone):
        raise VisitError(
            "Has enviado demasiadas solicitudes en poco tiempo. Espera unos minutos e inténtalo de nuevo."
        )

    now = now_local()
    epoch = time.time()
    try:
        return repository.register_visit(
            full_name=full_name,
            phone=phone,
            request_token=request_token,
            client_hash=client_hash,
            epoch=epoch,
            created_at=now.isoformat(timespec="seconds"),
        )
    except VisitError:
        raise
    except Exception as exc:
        raise VisitError(f"No se pudo guardar la visita: {exc}") from exc
