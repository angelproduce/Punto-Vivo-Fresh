"""Administrador de canales (YouTube, Instagram, Facebook, TikTok).

Un canal es una cuenta autorizada donde se publican los Shorts. Aquí se
guarda la configuración de cada uno (voz, estilo, categoría, horarios) y
los tokens de acceso, que NUNCA se imprimen ni se devuelven al navegador.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .config import env, get, path_of
from .util import STATE, info, read_json, warn, write_json

CHANNELS_FILE = STATE / "channels.json"
PLATFORMS = ("youtube", "instagram", "facebook", "tiktok")


def _load() -> list[dict]:
    data = read_json(CHANNELS_FILE, []) or []
    return [item for item in data if isinstance(item, dict)]


def _save(channels: list[dict]) -> None:
    write_json(CHANNELS_FILE, channels)


def list_channels(platform: str | None = None) -> list[dict]:
    items = _load()
    if platform:
        items = [c for c in items if c.get("platform") == platform]
    # Nunca exponer tokens al navegador.
    return [_public(c) for c in items]


def _public(channel: dict) -> dict:
    safe = dict(channel)
    safe.pop("access_token", None)
    safe.pop("refresh_token", None)
    safe["connected"] = bool(channel.get("access_token"))
    return safe


def get_channel(channel_id: str) -> dict | None:
    for channel in _load():
        if channel.get("id") == channel_id:
            return channel
    return None


def add_channel(platform: str, name: str, account_id: str = "",
                access_token: str = "", refresh_token: str = "",
                voice: str = "", style: str = "", category: str = "") -> dict:
    if platform not in PLATFORMS:
        raise ValueError(f"Plataforma no soportada: {platform}")
    channel = {
        "id": f"ch-{uuid.uuid4().hex[:8]}",
        "platform": platform,
        "name": name.strip() or account_id or platform,
        "account_id": account_id.strip(),
        "access_token": access_token.strip(),
        "refresh_token": refresh_token.strip(),
        "voice": voice.strip(),
        "style": style.strip(),
        "category": category.strip(),
        "enabled": True,
        "created_at": time.time(),
    }
    channels = _load()
    channels.append(channel)
    _save(channels)
    info(f"Canal añadido: {channel['name']} ({platform}).")
    return _public(channel)


def update_channel(channel_id: str, **fields) -> dict | None:
    channels = _load()
    for channel in channels:
        if channel.get("id") == channel_id:
            for key, value in fields.items():
                if key in {"access_token", "refresh_token", "name", "voice",
                           "style", "category", "enabled", "account_id"}:
                    channel[key] = value
            _save(channels)
            return _public(channel)
    return None


def remove_channel(channel_id: str) -> bool:
    channels = _load()
    before = len(channels)
    channels = [c for c in channels if c.get("id") != channel_id]
    _save(channels)
    return len(channels) < before


def verify_youtube(channel_id: str) -> dict:
    """Comprueba el token contra la API de YouTube y guarda el nombre real."""
    channel = get_channel(channel_id)
    if not channel or channel.get("platform") != "youtube":
        return {"ok": False, "error": "Canal no encontrado o no es de YouTube."}
    token = channel.get("access_token")
    if not token:
        return {"ok": False, "error": "Falta el token de acceso."}
    try:
        import urllib.request
        request = urllib.request.Request(
            "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        items = data.get("items") or []
        if not items:
            return {"ok": False, "error": "El token no da acceso a ningún canal."}
        snippet = items[0].get("snippet") or {}
        update_channel(channel_id, name=snippet.get("title") or channel.get("name"),
                      account_id=items[0].get("id"))
        return {"ok": True, "name": snippet.get("title"), "id": items[0].get("id")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def platforms() -> list[str]:
    return list(PLATFORMS)