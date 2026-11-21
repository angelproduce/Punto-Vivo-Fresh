"""OAuth 2.0 (con PKCE) para conectar redes sociales.

Guarda los tokens cifrados en `.state/oauth/`. Las credenciales de la app
(Client ID/Secret) se guardan cifradas en `.state/oauth_apps.json`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.fernet import Fernet

from .config import env, get, load_config
from .util import STATE, info, read_json, warn, write_json

OAUTH_DIR = STATE / "oauth"
APPS_FILE = STATE / "oauth_apps.json"
STATE_FILE = STATE / "oauth_state.json"
KEY_FILE = STATE / "oauth.key"

PROVIDERS = {
    "youtube": {
        "label": "YouTube",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.force-ssl",
        ],
        "extra_auth": {"access_type": "offline", "prompt": "consent"},
    },
    "tiktok": {
        "label": "TikTok",
        "auth_url": "https://www.tiktok.com/v2/auth/authorize/",
        "token_url": "https://open.tiktokapis.com/v2/oauth/token/",
        "scopes": ["user.info.basic", "video.publish", "video.upload"],
        "extra_auth": {},
    },
    "instagram": {
        "label": "Instagram",
        "auth_url": "https://www.instagram.com/oauth/authorize",
        "token_url": "https://api.instagram.com/oauth/access_token",
        "scopes": ["instagram_basic", "instagram_content_publish"],
        "extra_auth": {},
    },
    "facebook": {
        "label": "Facebook",
        "auth_url": "https://www.facebook.com/v20.0/dialog/oauth",
        "token_url": "https://graph.facebook.com/v20.0/oauth/access_token",
        "scopes": ["pages_manage_posts", "pages_read_engagement", "publish_video"],
        "extra_auth": {},
    },
}


def _key() -> bytes:
    env_key = env("OAUTH_ENCRYPTION_KEY")
    if env_key:
        try:
            return env_key.encode("utf-8")
        except Exception:
            pass
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes().strip()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    try:
        KEY_FILE.chmod(0o600)
    except Exception:
        pass
    return key


_FERNET = Fernet(_key())


def encrypt(value: str) -> str:
    return _FERNET.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _FERNET.decrypt(value.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


# ------------------------------------------------------------------ apps
def load_apps() -> dict:
    raw = read_json(APPS_FILE, {}) or {}
    out: dict = {}
    for provider, data in (raw or {}).items():
        if not isinstance(data, dict):
            continue
        out[provider] = {
            "client_id": decrypt(data.get("client_id", "")),
            "client_secret": decrypt(data.get("client_secret", "")),
            "redirect_uri": data.get("redirect_uri", ""),
        }
    return out


def save_app(provider: str, client_id: str, client_secret: str,
             redirect_uri: str = "") -> dict:
    if provider not in PROVIDERS:
        raise ValueError(f"Proveedor no soportado: {provider}")
    apps = load_apps()
    apps[provider] = {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "redirect_uri": redirect_uri.strip(),
    }
    # cifrar antes de guardar
    encrypted = {}
    for prov, data in apps.items():
        encrypted[prov] = {
            "client_id": encrypt(data["client_id"]),
            "client_secret": encrypt(data["client_secret"]),
            "redirect_uri": data["redirect_uri"],
        }
    write_json(APPS_FILE, encrypted)
    return {"provider": provider, "configured": bool(client_id.strip())}


def get_app(provider: str) -> dict:
    return load_apps().get(provider, {"client_id": "", "client_secret": "", "redirect_uri": ""})


# ------------------------------------------------------------------ pkce
def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")
    return verifier, challenge


def _state_store() -> dict:
    return read_json(STATE_FILE, {}) or {}


def _save_state_store(data: dict) -> None:
    write_json(STATE_FILE, data)


# ------------------------------------------------------------------ auth url
def start_auth(provider: str, redirect_uri: str | None = None) -> dict:
    if provider not in PROVIDERS:
        return {"ok": False, "error": "Proveedor no soportado."}
    app = get_app(provider)
    client_id = app.get("client_id")
    if not client_id:
        return {"ok": False, "error": f"Falta el Client ID de {PROVIDERS[provider]['label']}. Configúralo en Ajustes."}
    uri = redirect_uri or app.get("redirect_uri") or "http://127.0.0.1:8765/api/oauth/callback"
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(32)
    store = _state_store()
    store[state] = {
        "provider": provider,
        "verifier": verifier,
        "created_at": time.time(),
        "redirect_uri": uri,
    }
    # limpiar estados viejos (>10 min)
    now = time.time()
    store = {k: v for k, v in store.items() if now - float(v.get("created_at", 0)) < 600}
    _save_state_store(store)

    params = {
        "client_id": client_id,
        "redirect_uri": uri,
        "response_type": "code",
        "scope": " ".join(PROVIDERS[provider]["scopes"]),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    params.update(PROVIDERS[provider].get("extra_auth", {}))
    url = PROVIDERS[provider]["auth_url"] + "?" + urllib.parse.urlencode(params)
    return {"ok": True, "url": url, "state": state}


def validate_state(state: str) -> dict | None:
    store = _state_store()
    entry = store.get(state)
    if not entry:
        return None
    if time.time() - float(entry.get("created_at", 0)) > 600:
        return None
    return entry


def _consume_state(state: str) -> None:
    store = _state_store()
    store.pop(state, None)
    _save_state_store(store)


def exchange_code(provider: str, code: str, state: str) -> dict:
    entry = validate_state(state)
    if not entry or entry.get("provider") != provider:
        return {"ok": False, "error": "Estado OAuth inválido o expirado (posible CSRF)."}
    app = get_app(provider)
    client_id = app.get("client_id")
    client_secret = app.get("client_secret")
    if not client_id:
        return {"ok": False, "error": "Falta Client ID."}
    redirect_uri = entry.get("redirect_uri")
    verifier = entry.get("verifier")

    data = {
        "client_id": client_id,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if client_secret:
        data["client_secret"] = client_secret

    try:
        request = urllib.request.Request(
            PROVIDERS[provider]["token_url"],
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"ok": False, "error": f"Error al intercambiar el código: {str(exc)[:200]}"}

    access_token = payload.get("access_token")
    if not access_token:
        return {"ok": False, "error": payload.get("error_description") or "Sin access_token."}

    # Guardar token cifrado
    OAUTH_DIR.mkdir(parents=True, exist_ok=True)
    channel_id = f"{provider}-{secrets.token_hex(4)}"
    token_record = {
        "provider": provider,
        "channel_id": channel_id,
        "access_token": encrypt(str(access_token)),
        "refresh_token": encrypt(str(payload.get("refresh_token") or "")),
        "expires_at": time.time() + int(payload.get("expires_in") or 3600),
        "scope": payload.get("scope", ""),
        "created_at": time.time(),
        "client_id": encrypt(client_id),
        "client_secret": encrypt(client_secret),
    }
    (OAUTH_DIR / f"{channel_id}.json").write_text(
        json.dumps(token_record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _consume_state(state)
    info(f"Cuenta conectada: {PROVIDERS[provider]['label']} ({channel_id}).")
    return {"ok": True, "channel_id": channel_id, "provider": provider}


def list_connections(provider: str | None = None) -> list[dict]:
    if not OAUTH_DIR.exists():
        return []
    out: list[dict] = []
    for path in OAUTH_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if provider and data.get("provider") != provider:
            continue
        out.append({
            "channel_id": data.get("channel_id"),
            "provider": data.get("provider"),
            "label": PROVIDERS.get(data.get("provider", ""), {}).get("label", data.get("provider")),
            "connected": bool(data.get("access_token")),
            "expires_at": data.get("expires_at"),
            "expired": time.time() > float(data.get("expires_at") or 0),
            "scope": data.get("scope", ""),
        })
    return out


def disconnect(channel_id: str) -> bool:
    path = OAUTH_DIR / f"{channel_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def get_token(channel_id: str) -> str | None:
    path = OAUTH_DIR / f"{channel_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    access = decrypt(data.get("access_token", ""))
    if not access:
        return None
    if time.time() > float(data.get("expires_at") or 0) - 60:
        refreshed = refresh_token(channel_id)
        if refreshed:
            access = refreshed
    return access or None


def refresh_token(channel_id: str) -> str | None:
    path = OAUTH_DIR / f"{channel_id}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    refresh = decrypt(data.get("refresh_token", ""))
    client_id = decrypt(data.get("client_id", ""))
    client_secret = decrypt(data.get("client_secret", ""))
    provider = data.get("provider")
    if not (refresh and client_id and provider in PROVIDERS):
        return None
    payload = {
        "client_id": client_id,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }
    if client_secret:
        payload["client_secret"] = client_secret
    try:
        request = urllib.request.Request(
            PROVIDERS[provider]["token_url"],
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        warn(f"No se pudo renovar el token ({provider}): {str(exc)[:120]}")
        return None
    access = result.get("access_token")
    if not access:
        return None
    data["access_token"] = encrypt(str(access))
    if result.get("refresh_token"):
        data["refresh_token"] = encrypt(str(result["refresh_token"]))
    data["expires_at"] = time.time() + int(result.get("expires_in") or 3600)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(access)