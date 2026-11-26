"""Notificaciones Web Push para el administrador.

La clave pública VAPID se envía al navegador; la privada se usa solo en el
servidor. Si no están configuradas, la app sigue funcionando y el panel muestra
el aviso interno de siempre: la notificación nunca puede romper un pedido.
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlparse

try:
    from pywebpush import WebPushException, webpush
except Exception:  # la notificación es opcional: nunca debe romper la app
    WebPushException = None
    webpush = None

from . import config, repository


def configurado() -> bool:
    publica, privada = config.vapid_keys()
    return bool(publica and privada and webpush is not None)


def notificar_nuevo_pedido(numero: str, cliente: str, total: Any) -> int:
    """Avisa a todos los administradores suscritos. Devuelve los envíos logrados."""
    return _enviar({
        "title": "🔔 Nuevo pedido · PUNTO VIVO FRESH",
        "body": f"{numero} · {cliente} · RD$ {total}",
        "url": "/admin?section=pedidos",
        "tag": f"pvf-{numero}",
    })["enviados"]


def notificar_prueba() -> dict[str, Any]:
    """Aviso de prueba para comprobar el centro de notificaciones del dispositivo."""
    return _enviar({
        "title": "🔔 Prueba · PUNTO VIVO FRESH",
        "body": "Las notificaciones están activas en este dispositivo.",
        "url": "/admin?section=config-pagos",
        "tag": f"pvf-prueba-{int(time.time())}",
    })


def estado() -> dict[str, Any]:
    """Datos seguros para diagnosticar el panel: nunca incluye endpoints ni claves."""
    suscripciones = repository.listar_suscripciones_push()
    destinos: dict[str, int] = {}
    for suscripcion in suscripciones:
        host = (urlparse(suscripcion["endpoint"]).hostname or "desconocido").lower()
        destinos[host] = destinos.get(host, 0) + 1
    return {
        "configurado": configurado(),
        "suscripciones": len(suscripciones),
        "destinos": destinos,
    }


def _enviar(cuerpo: dict[str, Any]) -> dict[str, Any]:
    suscripciones = repository.listar_suscripciones_push()
    resultado: dict[str, Any] = {
        "configurado": configurado(),
        "suscripciones": len(suscripciones),
        "enviados": 0,
        "errores": 0,
    }
    if not resultado["configurado"]:
        return resultado
    _, privada = config.vapid_keys()
    datos = json.dumps(cuerpo)
    for suscripcion in suscripciones:
        try:
            webpush(
                subscription_info={
                    "endpoint": suscripcion["endpoint"],
                    "keys": {
                        "p256dh": suscripcion["p256dh"],
                        "auth": suscripcion["auth"],
                    },
                },
                data=datos,
                vapid_private_key=privada,
                vapid_claims={"sub": config.VAPID_SUBJECT},
                timeout=5,
            )
            repository.marcar_suscripcion_push(suscripcion["id"])
            resultado["enviados"] += 1
        except Exception as exc:
            resultado["errores"] += 1
            if WebPushException is not None and isinstance(exc, WebPushException):
                respuesta = getattr(exc, "response", None)
                if respuesta is not None and getattr(respuesta, "status_code", 0) in (404, 410):
                    repository.borrar_suscripcion_push(suscripcion["id"])
            # Un fallo de notificación jamás debe afectar al pedido confirmado.
            continue
    return resultado
