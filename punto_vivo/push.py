"""Notificaciones Web Push para el administrador.

La clave pública VAPID se envía al navegador; la privada se usa solo en el
servidor. Si no están configuradas, la app sigue funcionando y el panel muestra
el aviso interno de siempre: la notificación nunca puede romper un pedido.
"""
from __future__ import annotations

import json
import time
from typing import Any

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
    })


def notificar_prueba() -> int:
    """Aviso de prueba para comprobar el centro de notificaciones del dispositivo."""
    return _enviar({
        "title": "🔔 Prueba · PUNTO VIVO FRESH",
        "body": "Las notificaciones están activas en este dispositivo.",
        "url": "/admin?section=config-pagos",
        "tag": f"pvf-prueba-{int(time.time())}",
    })


def _enviar(cuerpo: dict[str, Any]) -> int:
    if not configurado():
        return 0
    _, privada = config.vapid_keys()
    datos = json.dumps(cuerpo)
    enviados = 0
    for suscripcion in repository.listar_suscripciones_push():
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
            enviados += 1
        except Exception as exc:
            if WebPushException is not None and isinstance(exc, WebPushException):
                respuesta = getattr(exc, "response", None)
                if respuesta is not None and getattr(respuesta, "status_code", 0) in (404, 410):
                    repository.borrar_suscripcion_push(suscripcion["id"])
            # Un fallo de notificación jamás debe afectar al pedido confirmado.
            continue
    return enviados
