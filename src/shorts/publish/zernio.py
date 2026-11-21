"""Publicación unificada con Zernio (un API key para 16+ redes).

Usa el SDK oficial `zernio-sdk` para cuentas y posts, y subida directa
por URL prefirmada para los vídeos (que superan los 4 MB).
Documentación: https://docs.zernio.com
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from pathlib import Path

from ..config import env, get
from ..util import info, warn

BASE = "https://zernio.com/api/v1"

try:
    from zernio import Zernio
    _SDK_AVAILABLE = True
except Exception:  # pragma: no cover
    _SDK_AVAILABLE = False
    Zernio = None  # type: ignore


def _client() -> "Zernio":
    if not _SDK_AVAILABLE:
        raise RuntimeError("El SDK de Zernio no está instalado (pip install zernio-sdk).")
    os.environ.setdefault("ZERNIO_API_KEY", env("ZERNIO_API_KEY"))
    return Zernio()


def ready(cfg: dict) -> bool:
    return bool(env("ZERNIO_API_KEY"))


def verify() -> dict:
    """Verifica la API key contra Zernio (GET /auth/verify)."""
    key = env("ZERNIO_API_KEY")
    if not key:
        return {"ok": False, "status": 0, "error": "Falta ZERNIO_API_KEY."}
    url = BASE + "/auth/verify"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.getcode()
            try:
                data = json.loads(response.read().decode("utf-8", "replace"))
            except Exception:
                data = {}
            return {"ok": True, "status": status, "data": data}
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        if status == 401:
            message = "API Key inválida o caducada."
        elif status == 403:
            message = "Permisos insuficientes."
        else:
            message = f"Error HTTP {status}."
        return {"ok": False, "status": status, "error": message, "detail": body[:200]}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": f"Error de red: {str(exc)[:120]}"}


def list_accounts() -> list[dict]:
    """Lista las cuentas conectadas en Zernio."""
    if not ready({}):
        return []
    try:
        client = _client()
        data = client.accounts.list_accounts()
    except Exception as exc:
        warn(f"Zernio: no pude listar cuentas ({str(exc)[:120]}).")
        return []
    accounts = data.get("accounts") or data.get("data") or []
    out: list[dict] = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        out.append({
            "platform": acc.get("platform") or acc.get("provider"),
            "accountId": acc.get("_id") or acc.get("id") or acc.get("accountId"),
            "username": acc.get("username") or acc.get("displayName") or acc.get("name"),
        })
    return out


def _upload_video(video: Path) -> str:
    """Sube el vídeo por URL prefirmada y devuelve la URL pública."""
    client = _client()
    presigned = client.media.get_media_presigned_url(
        filename=video.name, content_type="video/mp4", size=video.stat().st_size,
    )
    upload_url = (presigned.get("uploadUrl") or presigned.get("upload_url")
                  or presigned.get("url"))
    public_url = (presigned.get("publicUrl") or presigned.get("public_url")
                  or presigned.get("mediaUrl") or presigned.get("public_url"))
    if not upload_url:
        raise RuntimeError("Zernio no devolvió URL de subida.")
    with video.open("rb") as handle:
        data = handle.read()
    request = urllib.request.Request(upload_url, data=data, method="PUT")
    request.add_header("Content-Type", "video/mp4")
    with urllib.request.urlopen(request, timeout=300) as response:
        response.read()
    if not public_url:
        raise RuntimeError("Zernio no devolvió URL pública del vídeo.")
    return str(public_url)


def upload(cfg: dict, item: dict, folder: Path) -> dict:
    video = folder / "video.mp4"
    if not video.exists():
        raise FileNotFoundError("Falta video.mp4")
    if not ready(cfg):
        raise RuntimeError("Falta ZERNIO_API_KEY en .env")

    accounts = get(cfg, "publish.zernio.accounts", None)
    if not accounts:
        accounts = list_accounts()
    if not accounts:
        raise RuntimeError("No hay cuentas conectadas en Zernio. Conéctalas en su panel.")

    platforms = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        platform = acc.get("platform")
        account_id = acc.get("accountId") or acc.get("id")
        if platform and account_id:
            platforms.append({"platform": platform, "accountId": account_id})

    if not platforms:
        raise RuntimeError("No se pudo determinar las plataformas/cuentas de Zernio.")

    info("Subiendo el vídeo a Zernio…")
    public_url = _upload_video(video)

    title = str(item.get("title") or "Short")[:200]
    description = str(item.get("description") or item.get("title") or "")
    tags = [str(t).lstrip("#") for t in (item.get("hashtags") or [])]
    if tags:
        description = (description + "\n\n" + " ".join(f"#{t}" for t in tags))[:5000]

    client = _client()
    result = client.posts.create_post(
        content=description or title,
        media_items=[{"type": "video", "url": public_url}],
        platforms=platforms,
        publish_now=True,
    )
    post = result.get("post") if isinstance(result, dict) else None
    post_id = (post or {}).get("_id") or (post or {}).get("id") or (result.get("_id") if isinstance(result, dict) else None)
    return {"ok": True, "id": post_id, "platforms": [p["platform"] for p in platforms]}