"""Publicación: YouTube, TikTok y Facebook (opcional)."""
from __future__ import annotations

from pathlib import Path

from .. import queue
from ..config import get
from ..util import err, info, ok
from . import facebook, tiktok, youtube, zernio

ADAPTERS = {"youtube": youtube, "tiktok": tiktok, "facebook": facebook, "zernio": zernio}


def publish_approved(cfg: dict, dry_run: bool = False) -> dict:
    items = queue.list_shorts("approved")
    if not items:
        info("No hay shorts aprobados.")
        return {}

    platforms = [p for p in (get(cfg, "publish.platforms", []) or []) if p in ADAPTERS]
    if not platforms:
        raise RuntimeError("No hay plataformas configuradas en config.yaml.")

    ready = [p for p in platforms if ADAPTERS[p].ready(cfg)]
    if not dry_run and not ready:
        raise RuntimeError(
            "Ninguna plataforma está conectada. Configúrala en Ajustes o usa una simulación.")

    results: dict[str, dict] = {}
    for item in items:
        folder = Path(str(item.get("dir") or ""))
        outcome: dict[str, dict] = {}
        if dry_run:
            info(f"Simulando «{item.get('title')}»")
        for platform in platforms:
            if dry_run:
                outcome[platform] = {"ok": True, "id": "simulado"}
                continue
            if platform not in ready:
                outcome[platform] = {"ok": False, "error": "Falta conectar esta plataforma."}
                continue
            try:
                result = ADAPTERS[platform].upload(cfg, item, folder)
                result.setdefault("ok", True)
                outcome[platform] = result
                ok(f"{platform}: {result.get('url') or result.get('id') or 'subido'}")
            except Exception as exc:
                outcome[platform] = {"ok": False, "error": str(exc)[:300]}
                err(f"{platform}: {str(exc)[:160]}")
        results[item["id"]] = outcome
        if not dry_run and any(v.get("ok") for v in outcome.values()):
            queue.mark_published(item["id"], outcome)
    return results
