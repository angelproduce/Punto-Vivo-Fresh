"""Investigación de actualidad gratis: titulares y datos para el guion.

Fuentes sin claves ni cuentas:
  - Google News (RSS)      noticias recientes en español
  - Hacker News (Algolia)  novedades de tecnología
  - GitHub (API)           proyectos y lanzamientos
  - Wikipedia (API)        contexto y definiciones

Todo se guarda en `.cache/research` durante 6 horas, así que repetir un tema
no vuelve a descargar nada. Si no hay internet, devuelve lo que tenga en caché
o una lista vacía: el guion sigue funcionando con las plantillas.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from .config import get
from .util import CACHE, info, read_json, warn, write_json

RESEARCH_CACHE = CACHE / "research"
TTL = 6 * 3600
UA = {"User-Agent": "AngelProduceIA/1.0 (local; +https://localhost)"}
MAX_BRIEF = 2200


def _fetch(url: str, timeout: float = 15) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = html.unescape(text)
    # Google News añade enlaces y emojis al titular: fuera ambos.
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—|·")
    return text


def _strip_source(title: str, source: str) -> str:
    """Quita el « - Medio» que Google News pega al final del titular."""
    for suffix in (source, "Google News"):
        if suffix and title.endswith(f" - {suffix}"):
            title = title[: -(len(suffix) + 3)].strip()
    return title


def _news(query: str, limit: int) -> list[dict]:
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=es-419&gl=MX&ceid=MX:es-419"
    )
    root = ET.fromstring(_fetch(url))
    items: list[dict] = []
    for node in root.iter("item"):
        title = _clean(node.findtext("title"))
        if not title:
            continue
        source = _clean(node.findtext("source"))
        title = _strip_source(title, source)
        if not title:
            continue
        items.append({
            "source": source or "Google News",
            "title": title,
            "summary": "",
            "date": _clean(node.findtext("pubDate"))[:22],
            "url": (node.findtext("link") or "").strip(),
        })
        if len(items) >= limit:
            break
    return items


def _hn(query: str, limit: int) -> list[dict]:
    url = ("https://hn.algolia.com/api/v1/search?tags=story&hitsPerPage="
           f"{limit}&query={urllib.parse.quote(query)}")
    data = json.loads(_fetch(url).decode("utf-8", "replace"))
    items: list[dict] = []
    for hit in data.get("hits", [])[:limit]:
        title = _clean(hit.get("title") or hit.get("story_title"))
        if not title:
            continue
        items.append({
            "source": "Hacker News",
            "title": title,
            "summary": _clean(hit.get("story_text"))[:280],
            "date": str(hit.get("created_at") or "")[:10],
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        })
    return items


def _github(query: str, limit: int) -> list[dict]:
    url = ("https://api.github.com/search/repositories?sort=stars&order=desc&per_page="
           f"{limit}&q={urllib.parse.quote(query)}")
    data = json.loads(_fetch(url).decode("utf-8", "replace"))
    items: list[dict] = []
    for repo in data.get("items", [])[:limit]:
        name = repo.get("full_name")
        if not name:
            continue
        stars = int(repo.get("stargazers_count") or 0)
        items.append({
            "source": "GitHub",
            "title": f"{name} · {stars:,} estrellas".replace(",", "."),
            "summary": _clean(repo.get("description"))[:280],
            "date": str(repo.get("updated_at") or "")[:10],
            "url": repo.get("html_url") or "",
        })
    return items


def _wikipedia(query: str, limit: int) -> list[dict]:
    search = ("https://es.wikipedia.org/w/api.php?action=query&list=search&format=json"
              f"&srlimit={limit}&srsearch={urllib.parse.quote(query)}")
    found = json.loads(_fetch(search).decode("utf-8", "replace"))
    items: list[dict] = []
    for hit in found.get("query", {}).get("search", [])[:limit]:
        title = hit.get("title")
        if not title:
            continue
        try:
            summary = json.loads(
                _fetch("https://es.wikipedia.org/api/rest_v1/page/summary/"
                       + urllib.parse.quote(title.replace(" ", "_")))
                .decode("utf-8", "replace")
            )
        except Exception:
            continue
        extract = _clean(summary.get("extract"))
        if not extract:
            continue
        items.append({
            "source": "Wikipedia",
            "title": _clean(title),
            "summary": extract[:320],
            "date": "",
            "url": (summary.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
        })
    return items


def _youtube(query: str, limit: int) -> list[dict]:
    """Busca videos de YouTube y usa sus titulos/descripciones como informacion."""
    items: list[dict] = []
    try:
        import subprocess, sys, os
        node = r"C:\\Users\\angel\\Documents\\Default Project\\tools\\node\\node-v22.23.4-win-x64\\node.exe"
        cmd = [sys.executable, "-m", "yt_dlp"]
        if os.path.exists(node):
            cmd += ["--js-runtimes", f"node:{node}"]
        cmd += ["--remote-components", "ejs:github",
                f"ytsearch{limit}:{query}", "--dump-json", "--no-download",
                "--skip-download", "--quiet"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            title = data.get("title") or ""
            desc = (data.get("description") or "")[:200]
            if title:
                items.append({
                    "title": title,
                    "source": "YouTube",
                    "url": data.get("webpage_url") or "",
                    "summary": desc,
                })
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


_FETCHERS = {
    "news": _news,
    "hn": _hn,
    "github": _github,
    "wikipedia": _wikipedia,
    "youtube": _youtube,
}


def _queries(cfg: dict, topic: dict | None) -> list[str]:
    niche = str(get(cfg, "project.niche", "tecnología") or "tecnología")
    base = str((topic or {}).get("title") or (topic or {}).get("id") or niche)
    # Une el tema concreto con el nicho para que las fuentes no se vayan de contexto.
    words = base.replace("?", " ").replace("¿", " ").strip()
    return [words, f"{words} {niche}".strip()[:80]]


def gather(cfg: dict, topic: dict | None = None) -> list[dict]:
    """Titulares y datos actuales sobre el tema. Nunca lanza excepción."""
    if not bool(get(cfg, "script.research", True)):
        return []
    sources = get(cfg, "script.research_sources", None) or ["news", "hn", "github", "wikipedia"]
    if isinstance(sources, str):
        sources = [part.strip() for part in sources.split(",") if part.strip()]
    queries = _queries(cfg, topic)
    key = hashlib.sha1(
        ("|".join(queries) + "|" + ",".join(map(str, sources))).encode("utf-8")
    ).hexdigest()[:16]
    cached = RESEARCH_CACHE / f"{key}.json"
    if cached.exists():
        try:
            payload = read_json(cached, None)
            if isinstance(payload, dict) and time.time() - float(payload.get("at") or 0) < TTL:
                return [item for item in payload.get("items", []) if isinstance(item, dict)]
        except Exception:
            pass

    items: list[dict] = []
    seen: set[str] = set()
    per_source = max(2, int(get(cfg, "script.research_per_source", 4) or 4))
    try:
        deadline = time.time() + float(get(cfg, "script.research_timeout", 20) or 20)
    except (TypeError, ValueError):
        deadline = time.time() + 20
    for name in sources:
        if time.time() > deadline:
            break
        fetcher = _FETCHERS.get(str(name).lower())
        if not fetcher:
            continue
        for query in queries:
            if time.time() > deadline:
                break
            try:
                found = fetcher(query, per_source)
            except Exception as exc:
                warn(f"Investigación ({name}) no disponible: {str(exc)[:90]}")
                continue
            for item in found:
                title = _clean(item.get("title"))
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                item["title"] = title
                items.append(item)
            if len(items) >= per_source * 4:
                break
        if len(items) >= per_source * 4:
            break

    if items:
        RESEARCH_CACHE.mkdir(parents=True, exist_ok=True)
        write_json(cached, {"at": time.time(), "queries": queries, "items": items})
        info(f"Investigación: {len(items)} datos actuales de {', '.join(map(str, sources))}.")
    return items


def brief(items: list[dict], limit: int = MAX_BRIEF) -> str:
    """Convierte los hallazgos en un informe corto para el modelo de IA."""
    if not items:
        return "(sin datos externos; usa solo el tema)"
    lines: list[str] = []
    size = 0
    for index, item in enumerate(items, 1):
        date = f" ({item['date']})" if item.get("date") else ""
        summary = f": {item.get('summary')}" if item.get("summary") else ""
        line = f"{index}. [{item.get('source')}]{date} {item['title']}{summary}"
        if size + len(line) > limit:
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)
