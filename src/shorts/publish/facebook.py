"""Subida a una página de Facebook con un token de página."""
from __future__ import annotations

from pathlib import Path

import requests

from ..config import env, get


def ready(cfg: dict) -> bool:
    return bool(env("FACEBOOK_PAGE_TOKEN"))


def upload(cfg: dict, item: dict, folder: Path) -> dict:
    video = folder / "video.mp4"
    if not video.exists():
        raise FileNotFoundError("Falta video.mp4")
    token = env("FACEBOOK_PAGE_TOKEN")
    if not token:
        raise RuntimeError("Falta FACEBOOK_PAGE_TOKEN en .env")

    description = str(item.get("description") or item.get("title") or "")
    tags = [str(t).lstrip("#") for t in (item.get("hashtags") or [])]
    if tags:
        description += "\n\n" + " ".join(f"#{t}" for t in tags)

    page = get(cfg, "publish.facebook.page_id", "me") or "me"
    with video.open("rb") as handle:
        response = requests.post(
            f"https://graph-video.facebook.com/v20.0/{page}/videos",
            data={
                "access_token": token,
                "description": description[:5000],
                "title": str(item.get("title") or "Short")[:200],
            },
            files={"source": (video.name, handle, "video/mp4")},
            timeout=900,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Facebook {response.status_code}: {response.text[:200]}")
    payload = response.json()
    return {"ok": True, "id": str(payload.get("id", ""))}
