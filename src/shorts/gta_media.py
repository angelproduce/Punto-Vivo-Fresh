"""GTA Media Center: noticias, imágenes y videos de GTA 6 / Rockstar.

Integra NewsAPI, IGDB y YouTube Data API. Las claves se leen del entorno
(.env) o de la configuración segura del backend. Nunca se exponen al frontend.
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .config import env, get, load_config
from .util import CACHE, info, read_json, warn, write_json

# Asegura que .env se haya cargado aunque este módulo se importe
# antes de que se llame a load_config().
try:
    load_config()
except Exception:
    pass

GTA_CACHE = CACHE / "gta_media"
TTL = 3 * 3600
UA = {"User-Agent": "AngelProduceIA/1.0 (local)"}


def _fetch_json(url: str, headers: dict | None = None, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _cache_key(prefix: str, params: dict) -> str:
    raw = prefix + "|" + json.dumps(params, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _cached(prefix: str, params: dict) -> Any | None:
    key = _cache_key(prefix, params)
    path = GTA_CACHE / f"{prefix}-{key}.json"
    if not path.exists():
        return None
    try:
        payload = read_json(path, None)
        if isinstance(payload, dict) and time.time() - float(payload.get("at", 0)) < TTL:
            return payload.get("data")
    except Exception:
        pass
    return None


def _save_cache(prefix: str, params: dict, data: Any) -> None:
    GTA_CACHE.mkdir(parents=True, exist_ok=True)
    key = _cache_key(prefix, params)
    path = GTA_CACHE / f"{prefix}-{key}.json"
    try:
        write_json(path, {"at": time.time(), "params": params, "data": data})
    except Exception:
        pass


# ------------------------------------------------------------------ NewsAPI
def newsapi_search(query: str = "GTA 6 OR Rockstar Games", sort_by: str = "publishedAt",
                  from_date: str | None = None, page_size: int = 20) -> list[dict]:
    key = env("NEWSAPI_KEY") or env("NEWS_API_KEY")
    if not key:
        return []
    params = {"q": query, "sortBy": sort_by, "pageSize": page_size, "language": "es"}
    if from_date:
        params["from"] = from_date
    cached = _cached("newsapi", params)
    if cached is not None:
        return cached
    url = "https://newsapi.org/v2/everything?" + urllib.parse.urlencode(params) + f"&apiKey={key}"
    try:
        data = _fetch_json(url)
    except Exception as exc:
        warn(f"NewsAPI no disponible: {str(exc)[:120]}")
        return []
    items = []
    seen: set[str] = set()
    for art in data.get("articles", []) or []:
        title = (art.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        items.append({
            "type": "news",
            "source": "NewsAPI",
            "title": title,
            "description": (art.get("description") or "").strip(),
            "url": art.get("url") or "",
            "image": art.get("urlToImage") or "",
            "publishedAt": art.get("publishedAt") or "",
            "author": art.get("author") or art.get("source", {}).get("name", ""),
        })
    _save_cache("newsapi", params, items)
    return items


# ------------------------------------------------------------------ IGDB
_IGDB_TOKEN: dict[str, Any] = {"token": None, "expires": 0}


def _igdb_token() -> str | None:
    cid = env("IGDB_CLIENT_ID") or env("TWITCH_CLIENT_ID")
    secret = env("IGDB_CLIENT_SECRET") or env("TWITCH_CLIENT_SECRET")
    if not cid or not secret:
        return None
    if _IGDB_TOKEN["token"] and time.time() < _IGDB_TOKEN["expires"] - 60:
        return _IGDB_TOKEN["token"]
    try:
        import urllib.parse
        url = "https://id.twitch.tv/oauth2/token"
        payload = urllib.parse.urlencode({
            "client_id": cid,
            "client_secret": secret,
            "grant_type": "client_credentials",
        }).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        _IGDB_TOKEN["token"] = data.get("access_token")
        _IGDB_TOKEN["expires"] = time.time() + int(data.get("expires_in", 3600))
        return _IGDB_TOKEN["token"]
    except Exception as exc:
        warn(f"IGDB token error: {str(exc)[:120]}")
        return None


def igdb_search(query: str = "Grand Theft Auto", limit: int = 20) -> list[dict]:
    token = _igdb_token()
    cid = env("IGDB_CLIENT_ID") or env("TWITCH_CLIENT_ID")
    if not token or not cid:
        return []
    params = {"q": query, "limit": limit}
    cached = _cached("igdb", params)
    if cached is not None:
        return cached
    # IGDB usa POST con body Apicalypse.
    body = (f'search "{query}"; fields name,cover.url,artworks.url,screenshots.url,'
            f"first_release_date,summary,rating; limit {limit};")
    url = "https://api.igdb.com/v4/games"
    try:
        req = urllib.request.Request(url, data=body.encode(), headers={
            "Client-ID": cid,
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
            "User-Agent": "AngelProduceIA/1.0",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        warn(f"IGDB no disponible: {str(exc)[:120]}")
        return []
    items = []
    seen: set[str] = set()
    for game in data or []:
        name = game.get("name") or ""
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        images = []
        cover = game.get("cover", {}) or {}
        if cover.get("url"):
            images.append("https:" + cover["url"].replace("t_thumb", "t_1080p"))
        for art in (game.get("artworks") or []):
            if art.get("url"):
                images.append("https:" + art["url"].replace("t_thumb", "t_1080p"))
        for sc in (game.get("screenshots") or []):
            if sc.get("url"):
                images.append("https:" + sc["url"].replace("t_thumb", "t_1080p"))
        items.append({
            "type": "image",
            "source": "IGDB",
            "title": name,
            "description": (game.get("summary") or "")[:300],
            "images": images,
            "rating": game.get("rating"),
            "release_date": game.get("first_release_date"),
        })
    _save_cache("igdb", params, items)
    return items


# ------------------------------------------------------------------ YouTube Data API
def youtube_search(query: str = "GTA 6 trailer", max_results: int = 15,
                  order: str = "relevance", video_type: str = "video") -> list[dict]:
    key = env("YOUTUBE_API_KEY") or env("GOOGLE_API_KEY")
    if not key:
        return []
    params = {
        "part": "snippet",
        "q": query,
        "type": video_type,
        "maxResults": max_results,
        "order": order,
        "key": key,
        "relevanceLanguage": "es",
        "regionCode": "MX",
    }
    cached = _cached("youtube", params)
    if cached is not None:
        return cached
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
    try:
        data = _fetch_json(url)
    except Exception as exc:
        warn(f"YouTube API no disponible: {str(exc)[:120]}")
        return []
    items = []
    seen: set[str] = set()
    for v in data.get("items", []) or []:
        vid = (v.get("id") or {}).get("videoId")
        title = (v.get("snippet") or {}).get("title") or ""
        if not vid or title.lower() in seen:
            continue
        seen.add(title.lower())
        items.append({
            "type": "video",
            "source": "YouTube",
            "title": title,
            "description": (v.get("snippet") or {}).get("description") or "",
            "videoId": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail": ((v.get("snippet") or {}).get("thumbnails") or {}).get("high", {}).get("url", ""),
            "channel": (v.get("snippet") or {}).get("channelTitle") or "",
            "publishedAt": (v.get("snippet") or {}).get("publishedAt") or "",
        })
    _save_cache("youtube", params, items)
    return items


# ------------------------------------------------------------------ Agregador
def search_all(query: str = "GTA 6", sort: str = "relevance") -> dict:
    """Busca en las tres fuentes y devuelve resultados agrupados + dedup global."""
    news = newsapi_search(query + " OR Rockstar Games", sort_by="publishedAt")
    images = igdb_search(query)
    videos = youtube_search(query + " trailer gameplay", order=sort)
    # Dedup global por título normalizado.
    seen: set[str] = set()
    dedup_news, dedup_images, dedup_videos = [], [], []
    for item in news:
        key = _norm(item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        dedup_news.append(item)
    for item in images:
        key = _norm(item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        dedup_images.append(item)
    for item in videos:
        key = _norm(item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        dedup_videos.append(item)
    return {
        "query": query,
        "news": dedup_news,
        "images": dedup_images,
        "videos": dedup_videos,
        "counts": {"news": len(dedup_news), "images": len(dedup_images), "videos": len(dedup_videos)},
    }


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


# ------------------------------------------------------------------ Test connections
def test_newsapi() -> dict:
    key = env("NEWSAPI_KEY") or env("NEWS_API_KEY")
    if not key:
        return {"ok": False, "error": "Falta NEWSAPI_KEY"}
    try:
        data = _fetch_json(f"https://newsapi.org/v2/top-headlines?country=us&pageSize=1&apiKey={key}")
        if data.get("status") == "ok":
            return {"ok": True, "total": data.get("totalResults", 0)}
        return {"ok": False, "error": data.get("message", "error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def test_igdb() -> dict:
    token = _igdb_token()
    if not token:
        return {"ok": False, "error": "Faltan IGDB_CLIENT_ID / IGDB_CLIENT_SECRET"}
    return {"ok": True}


def test_youtube() -> dict:
    key = env("YOUTUBE_API_KEY") or env("GOOGLE_API_KEY")
    if not key:
        return {"ok": False, "error": "Falta YOUTUBE_API_KEY"}
    try:
        data = _fetch_json(f"https://www.googleapis.com/youtube/v3/search?part=snippet&q=GTA+6&maxResults=1&key={key}")
        if "items" in data:
            return {"ok": True, "total": data.get("pageInfo", {}).get("totalResults", 0)}
        return {"ok": False, "error": data.get("error", {}).get("message", "error")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
