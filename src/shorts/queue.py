"""Cola de vídeos: pendiente → aprobado → publicado."""
from __future__ import annotations

import shutil
from pathlib import Path

from .util import (APPROVED, PENDING, PUBLISHED, read_json, shorten, slugify,
                   stamp, today, write_json)


def _status_dir(status: str) -> Path:
    return {"pending": PENDING, "approved": APPROVED, "published": PUBLISHED}[status]


def _folders(status: str) -> list[Path]:
    folder = _status_dir(status)
    if not folder.exists():
        return []
    return [p for p in folder.iterdir() if p.is_dir()]


def existing_ids() -> set[str]:
    ids: set[str] = set()
    for status in ("pending", "approved", "published"):
        ids.update(p.name for p in _folders(status))
    return ids


def new_id(title: str) -> str:
    base = f"{today()}-{slugify(title, 40)}"
    candidate = base
    counter = 2
    ids = existing_ids()
    while candidate in ids:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def save_short(script, video: Path, cover: Path, duration: float,
               platforms: list[str] | None = None) -> Path:
    short_id = new_id(script.title)
    folder = PENDING / short_id
    folder.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(video, folder / "video.mp4")
    if Path(cover).exists():
        shutil.copyfile(cover, folder / "cover.jpg")

    meta = {
        "id": short_id,
        "title": script.title,
        "description": script.description,
        "hashtags": list(script.hashtags),
        "hook": script.hook,
        "text": script.full_text,
        "topic_id": script.topic_id,
        "duration": round(float(duration), 2),
        "created_at": stamp(),
        "status": "pending",
        "platforms": platforms or [],
        "published": {},
        "dir": str(folder),
    }
    write_json(folder / "meta.json", meta)
    return folder


def list_shorts(status: str) -> list[dict]:
    items: list[dict] = []
    for folder in _folders(status):
        meta = read_json(folder / "meta.json")
        if not isinstance(meta, dict):
            continue
        meta.setdefault("id", folder.name)
        meta.setdefault("title", folder.name)
        meta.setdefault("duration", 0)
        meta["dir"] = str(folder)
        meta["status"] = status
        items.append(meta)
    items.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    return items


def find_short(short_id: str) -> tuple[str | None, dict | None]:
    for status in ("pending", "approved", "published"):
        folder = _status_dir(status) / short_id
        meta = read_json(folder / "meta.json")
        if isinstance(meta, dict):
            meta.setdefault("id", short_id)
            meta["dir"] = str(folder)
            return status, meta
        if folder.exists():
            return status, {"id": short_id, "title": short_id, "dir": str(folder)}
    return None, None


def approve_short(short_id: str) -> Path:
    source = PENDING / short_id
    if not source.exists():
        raise FileNotFoundError("Ese short ya no está en pendientes.")
    target = APPROVED / short_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(source), str(target))
    meta = read_json(target / "meta.json") or {}
    meta["status"] = "approved"
    meta["dir"] = str(target)
    meta.setdefault("published", {})
    write_json(target / "meta.json", meta)
    return target


def reject_short(short_id: str) -> None:
    folder = PENDING / short_id
    if folder.exists():
        shutil.rmtree(folder)


def delete_short(short_id: str) -> bool:
    """Elimina un short de cualquier estado (pendiente, aprobado o publicado)."""
    removed = False
    for status in ("pending", "approved", "published"):
        folder = _status_dir(status) / short_id
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)
            removed = True
    return removed


def mark_published(short_id: str, results: dict) -> Path:
    source = APPROVED / short_id
    if not source.exists():
        raise FileNotFoundError("Ese short no está aprobado.")
    target = PUBLISHED / short_id
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(source), str(target))
    meta = read_json(target / "meta.json") or {}
    meta["status"] = "published"
    meta["published"] = results
    meta["published_at"] = stamp()
    meta["dir"] = str(target)
    write_json(target / "meta.json", meta)
    return target


def summary(status: str) -> str:
    items = list_shorts(status)
    if not items:
        return f"({status}) vacío"
    return "\n".join(
        f"  {item['id']:<44} {shorten(item.get('title', ''), 44)}" for item in items
    )
