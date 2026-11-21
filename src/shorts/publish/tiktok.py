"""Subida a TikTok (Content Posting API con token propio)."""
from __future__ import annotations

import json
from pathlib import Path

import requests

from ..config import get, path_of

INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"


def ready(cfg: dict) -> bool:
    return path_of(get(cfg, "publish.tiktok.token", "state/tiktok_token.json")).exists()


def _token(cfg: dict) -> str:
    path = path_of(get(cfg, "publish.tiktok.token", "state/tiktok_token.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    token = data.get("access_token") or data.get("token")
    if not token:
        raise RuntimeError("El token de TikTok no tiene access_token.")
    return str(token)


def upload(cfg: dict, item: dict, folder: Path) -> dict:
    video = folder / "video.mp4"
    if not video.exists():
        raise FileNotFoundError("Falta video.mp4")
    size = video.stat().st_size
    if size > 64 * 1024 * 1024:
        raise RuntimeError("El vídeo supera los 64 MB: usa el flujo por trozos.")

    title = str(item.get("title") or "Short")[:150]
    token = _token(cfg)

    init = requests.post(
        INIT_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "post_info": {
                "title": title,
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_comment": False,
                "disable_duet": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": size,
                "total_chunk_count": 1,
            },
        },
        timeout=60,
    )
    payload = init.json() if init.content else {}
    if init.status_code >= 400 or payload.get("error", {}).get("code"):
        message = payload.get("error", {}).get("message") or init.text[:200]
        raise RuntimeError(f"TikTok {init.status_code}: {message}")

    data = payload.get("data") or {}
    upload_url = data.get("upload_url")
    publish_id = data.get("publish_id", "")
    if not upload_url:
        raise RuntimeError("TikTok no devolvió una URL de subida.")

    with video.open("rb") as handle:
        push = requests.put(
            upload_url,
            headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(size),
                "Content-Range": f"bytes 0-{size - 1}/{size}",
            },
            data=handle,
            timeout=600,
        )
    if push.status_code >= 400:
        raise RuntimeError(f"TikTok upload {push.status_code}: {push.text[:200]}")
    return {"ok": True, "id": publish_id}
