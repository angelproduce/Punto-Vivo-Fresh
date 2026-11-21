"""Inteligencia para YouTube: títulos, descripciones, hashtags y análisis.

Todo lo que requiera IA usa los mismos proveedores que el resto del proyecto
(Gemini, OpenAI, GitHub Models) y cae a plantillas si no hay claves.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from . import research, script_gen
from .config import env, get, load_config
from .util import info, read_json, warn, write_json


CACHE = Path("unused")


def _cfg() -> dict:
    return load_config()


def _ask(prompt: str) -> str | None:
    cfg = _cfg()
    for name in script_gen.provider_order(cfg, include_fallback=False):
        if not script_gen.provider_ready(name):
            continue
        try:
            if name == "gemini":
                return script_gen._gemini(cfg, prompt)
            if name == "openai":
                return script_gen._openai(cfg, prompt)
            if name == "github":
                return script_gen._github(cfg, prompt)
        except Exception as exc:
            warn(f"{name} no respondió: {str(exc)[:80]}")
    return None


def suggest_title(script_text: str, niche: str = "") -> str:
    niche = niche or get(_cfg(), "project.niche", "tecnología")
    prompt = (
        "Eres experto en YouTube Shorts en español. "
        "Devuelve SOLO un título de máximo 60 caracteres, sin comillas ni emojis, "
        "que genere curiosidad y tenga palabras clave. "
        f"Nicho: {niche}. Contenido: {script_text[:600]}"
    )
    text = _ask(prompt)
    if text:
        line = text.strip().strip('"').strip("'")
        line = re.sub(r"\s+", " ", line)[:60]
        if line:
            return line
    # Plantilla local.
    words = re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", script_text)[:6]
    return " ".join(words).capitalize()[:60] or "Short de tecnología"


def suggest_description(script_text: str, title: str = "") -> str:
    niche = get(_cfg(), "project.niche", "tecnología")
    prompt = (
        "Escribe una descripción de YouTube Shorts en español (máximo 300 caracteres), "
        "con una frase inicial atractiva, una llamada a la acción y 3 hashtags al final. "
        f"Título: {title}. Contenido: {script_text[:700]}. Nicho: {niche}."
    )
    text = _ask(prompt)
    if text:
        return text.strip()[:500]
    return f"{title}\n\nSuscríbete para más contenido de {niche}.\n#shorts #{niche.replace(' ', '')}"


def suggest_hashtags(script_text: str, count: int = 8) -> list[str]:
    niche = get(_cfg(), "project.niche", "tecnología")
    prompt = (
        f"Devuelve SOLO una lista JSON de {count} hashtags (sin #) en español para un "
        f"Short de YouTube sobre: {script_text[:500]}. Nicho: {niche}."
    )
    text = _ask(prompt)
    if text:
        try:
            data = json.loads(text[text.find("["):text.rfind("]") + 1])
            if isinstance(data, list):
                return [str(x).lstrip("#") for x in data][:count]
        except Exception:
            pass
    # Plantilla local.
    base = ["shorts", "tecnologia", "gta6", "gaming", "ia"]
    words = [w.lower() for w in re.findall(r"[\wáéíóúüñ]+", script_text)]
    extra = [w for w in words if len(w) > 4][:count]
    return list(dict.fromkeys(base + extra))[:count]


def keyword_ideas(topic: str, limit: int = 10) -> list[str]:
    """Ideas de palabras clave combinando el tema con la investigación."""
    cfg = _cfg()
    items = research.gather(cfg, {"title": topic, "id": topic})
    ideas = [topic]
    for item in items[:limit]:
        title = str(item.get("title") or "").strip()
        if title:
            ideas.append(title[:60])
    # Dedup manteniendo orden.
    seen: set[str] = set()
    out: list[str] = []
    for idea in ideas:
        key = idea.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(idea)
    return out[:limit]


def channel_stats(access_token: str) -> dict:
    """Estadísticas básicas de un canal de YouTube (requiere OAuth)."""
    if not access_token:
        return {"ok": False, "error": "Falta el token."}
    try:
        url = ("https://www.googleapis.com/youtube/v3/channels"
               "?part=statistics,snippet&mine=true")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
        items = data.get("items") or []
        if not items:
            return {"ok": False, "error": "Sin datos."}
        item = items[0]
        stats = item.get("statistics") or {}
        return {
            "ok": True,
            "title": (item.get("snippet") or {}).get("title"),
            "subscribers": int(stats.get("subscriberCount") or 0),
            "views": int(stats.get("viewCount") or 0),
            "videos": int(stats.get("videoCount") or 0),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def analyze_retention(_video_id: str) -> dict:
    """YouTube no expone retención por vídeo a otros canales; solo al dueño.

    Devuelve un marcador honesto: los datos reales requieren OAuth del canal
    y la YouTube Analytics API.
    """
    return {
        "ok": False,
        "note": "La retención real requiere OAuth del canal y YouTube Analytics API. "
                "No se inventan métricas.",
    }