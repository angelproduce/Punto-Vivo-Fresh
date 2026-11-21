"""Subida a YouTube con OAuth ya autorizado (state/youtube_token.json)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import requests

from ..config import get, path_of


def ready(cfg: dict) -> bool:
    return path_of(get(cfg, "publish.youtube.token", "state/youtube_token.json")).exists()


def _access_token(cfg: dict) -> str:
    token_path = path_of(get(cfg, "publish.youtube.token", "state/youtube_token.json"))
    data = json.loads(token_path.read_text(encoding="utf-8"))
    if data.get("access_token"):
        return str(data["access_token"])
    refresh = data.get("refresh_token")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if not (refresh and client_id and client_secret):
        raise RuntimeError("El token de YouTube no tiene refresh_token/client_id/client_secret.")
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    data["access_token"] = token
    token_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return str(token)


def upload(cfg: dict, item: dict, folder: Path) -> dict:
    video = folder / "video.mp4"
    if not video.exists():
        raise FileNotFoundError("Falta video.mp4")
    token = _access_token(cfg)

    title = str(item.get("title") or "Short")
    if "#shorts" not in title.lower():
        title = f"{title} #Shorts"
    title = title[:100]
    tags = [str(t).lstrip("#") for t in (item.get("hashtags") or [])][:15]
    description = str(item.get("description") or "")
    if tags:
        description += "\n\n" + " ".join(f"#{t}" for t in tags)
    description = description.strip()[:4900]

    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    boundary = "shorts-" + uuid.uuid4().hex
    body = bytearray()
    body += f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
    body += json.dumps(metadata, ensure_ascii=False).encode("utf-8")
    body += f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode()
    body += video.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()

    response = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "multipart", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        data=bytes(body),
        timeout=600,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"YouTube {response.status_code}: {response.text[:200]}")
    video_id = response.json().get("id", "")
    return {"ok": True, "id": video_id, "url": f"https://youtu.be/{video_id}"}
