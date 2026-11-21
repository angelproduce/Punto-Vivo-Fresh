"""Recursos visuales reales y relevantes para cada escena del guion.

Fuentes (todas con búsqueda por consulta concreta):
  - YouTube          fragmentos de noticias / acontecimientos
  - Pexels           vídeos y fotos
  - Pixabay          vídeos y fotos
  - Unsplash         fotografía
  - Wikimedia Commons material audiovisual libre
  - Openverse        imágenes con licencia Creative Commons

Reglas importantes:
  * Nunca se generan degradados, fondos negros ni plantillas abstractas.
  * Cada clip se busca con la consulta visual de SU escena, no con una
    consulta global repetida.
  * Se descartan resultados cuyo título/tags no tengan relación con la
    consulta y los fondos abstractos ("gradient", "abstract", "wallpaper"...).
  * La caché de YouTube es por consulta (hash), así que nunca se reutiliza
    un vídeo descargado para otro tema.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import env, get
from .keywords import (GENERIC_VISUAL as _GENERIC_VISUAL,
                       NAMED as _NAMED,
                       main_subject as _main_subject,
                       not_abstract as _not_abstract, plain as _plain,
                       plain_norm as _plain_norm,
                       person_subject as _person_subject,
                       relevant as _relevant_base, scene_query as _scene_query,
                       subject_tokens as _subject_tokens,
                       tokens as _tokens, translate as _translate_query,
                       WEAK_VISUAL as _WEAK_VISUAL)
from .util import (CACHE, IMAGE_EXT, VIDEO_EXT, ffmpeg, ffmpeg_exe, info,
                   probe_duration, warn, write_json)

MEDIA_CACHE = CACHE / "media"
UA = {"User-Agent": "AngelProduceIA/1.0 (local news editor)"}
MAX_DOWNLOAD = 140 * 1024 * 1024
WIKI_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"

# Wikimedia responde 429 si se piden muchas fichas seguidas. En lugar de
# encadenar reintentos largos (que congelan el render varios minutos), se pone
# el host en cuarentena unos segundos y se sigue con las demás fuentes.
_HOST_COOLDOWN: dict[str, float] = {}
_COOLDOWN_SECONDS = 25.0


def _host(url: str) -> str:
    match = re.match(r"https?://([^/]+)", str(url or ""))
    host = match.group(1).lower() if match else ""
    # commons.wikimedia.org (API) y upload.wikimedia.org (archivos) comparten
    # la misma cuota: se tratan como un único host.
    return "wikimedia" if host.endswith(".wikimedia.org") or host == "wikimedia.org" else host


def _host_blocked(url: str) -> bool:
    return _HOST_COOLDOWN.get(_host(url), 0.0) > time.time()


def _host_penalize(url: str, seconds: float = _COOLDOWN_SECONDS) -> None:
    _HOST_COOLDOWN[_host(url)] = time.time() + max(0.0, seconds)

@dataclass
class MediaItem:
    path: Path
    source: str
    kind: str            # video | image
    query: str = ""
    title: str = ""
    license: str = ""
    page: str = ""       # URL de la ficha original (atribución)


# ------------------------------------------------------------------ helpers
def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value))[:70].strip("-") or "item"


def _download(url: str, target: Path, min_size: int = 4096,
              max_bytes: int = MAX_DOWNLOAD) -> bool:
    if not url:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size >= min_size:
        return True
    if _host_blocked(url):
        return False
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        for attempt in range(3):
            try:
                with requests.get(url, stream=True, timeout=90, headers=UA) as response:
                    response.raise_for_status()
                    size = 0
                    with tmp.open("wb") as handle:
                        for chunk in response.iter_content(262144):
                            if not chunk:
                                continue
                            size += len(chunk)
                            if size > max_bytes:
                                raise RuntimeError("archivo demasiado grande")
                            handle.write(chunk)
                break
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status == 429:
                    _host_penalize(url)
                    if attempt < 2:
                        tmp.unlink(missing_ok=True)
                        time.sleep(1.2 * (attempt + 1))
                        continue
                raise
        if tmp.stat().st_size < min_size:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(target)
        return True
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        warn(f"Descarga fallida ({target.name}): {str(exc)[:80]}")
        return False


def _get(url: str, *, params: dict | None = None, headers: dict | None = None,
         timeout: int = 30, tries: int = 4):
    """GET con reintento: Wikimedia/Openverse limitan peticiones (429)."""
    if _host_blocked(url):
        raise RuntimeError(f"cuota temporal agotada ({_host(url)})")
    delay = 2.0
    last: Exception | None = None
    for attempt in range(tries):
        try:
            response = requests.get(url, params=params, headers=headers,
                                    timeout=timeout)
            if response.status_code == 429 and attempt < tries - 1:
                # Respeta `Retry-After` si el servidor lo indica.
                _host_penalize(url)
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after else delay
                except ValueError:
                    wait = delay
                time.sleep(min(20.0, max(1.0, wait)))
                delay *= 2
                continue
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retry = status is None or status == 429 or status >= 500
            if status == 429:
                _host_penalize(url)
            elif (status or 0) >= 500 and attempt >= tries - 1:
                # Puerta de enlace caída (502/503/504): no insistir en cada escena.
                _host_penalize(url, 15.0)
            if retry and attempt < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise last or RuntimeError("petición fallida")


def _ext_for(mime: str, fallback: str = ".jpg") -> str:
    mime = (mime or "").lower()
    if "mp4" in mime:
        return ".mp4"
    if "webm" in mime:
        return ".webm"
    if "ogg" in mime or "ogv" in mime:
        return ".ogv"
    if "png" in mime:
        return ".png"
    if "webp" in mime:
        return ".webp"
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    return fallback


# ------------------------------------------------------------------ fuentes
def _pexels_videos(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    key = env("PEXELS_API_KEY")
    if not key:
        return []
    response = requests.get(
        "https://api.pexels.com/videos/search",
        params={"query": query, "orientation": "portrait",
                "per_page": max(15, count * 5), "size": "medium"},
        headers={"Authorization": key},
        timeout=25,
    )
    response.raise_for_status()
    items: list[MediaItem] = []
    for video in response.json().get("videos", []):
        # La API no da título, pero la URL sí trae un slug descriptivo:
        # lo usamos para no aceptar un vídeo sin relación con la escena.
        slug = _plain(str(video.get("url") or "").replace("-", " "))
        if not _relevant(slug, query) or not _not_abstract(slug, query):
            continue
        files = [f for f in video.get("video_files", [])
                 if f.get("height") and f.get("width")
                 and f["height"] >= 900 and f["width"] < f["height"]]
        if not files:
            continue
        files.sort(key=lambda f: f.get("height", 0))
        url = files[0].get("link")
        if not url:
            continue
        target = MEDIA_CACHE / f"pexels-v-{video.get('id')}.mp4"
        if str(target) in exclude:
            continue
        if _download(url, target, 20000):
            page = str(video.get("url") or "")
            items.append(MediaItem(target, "Pexels", "video", query,
                                   f"Pexels video {video.get('id')}",
                                   "Pexels License", page))
        if len(items) >= count:
            break
    return items


def _pexels_photos(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    key = env("PEXELS_API_KEY")
    if not key:
        return []
    response = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "orientation": "portrait",
                "per_page": max(15, count * 5)},
        headers={"Authorization": key},
        timeout=25,
    )
    response.raise_for_status()
    items: list[MediaItem] = []
    for photo in response.json().get("photos", []):
        alt = str(photo.get("alt") or "")
        if not _relevant(alt, query) or not _not_abstract(alt, query):
            continue
        urls = photo.get("src") or {}
        url = urls.get("large2x") or urls.get("portrait") or urls.get("large")
        target = MEDIA_CACHE / f"pexels-p-{photo.get('id')}.jpg"
        if str(target) in exclude:
            continue
        if _download(url, target, 2048):
            items.append(MediaItem(target, "Pexels", "image", query, alt,
                                   "Pexels License",
                                   str(photo.get("url") or "")))
        if len(items) >= count:
            break
    return items


def _pixabay_videos(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    key = env("PIXABAY_API_KEY")
    if not key:
        return []
    response = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": key, "q": query, "video_type": "film",
                "per_page": max(20, count * 5), "safesearch": "true"},
        timeout=25,
    )
    response.raise_for_status()
    items: list[MediaItem] = []
    for hit in response.json().get("hits", []):
        tags = str(hit.get("tags") or "")
        if not _relevant(tags, query) or not _not_abstract(tags, query):
            continue
        videos = hit.get("videos") or {}
        best = None
        for name in ("medium", "large", "small"):
            candidate = videos.get(name) or {}
            if (candidate.get("url") and candidate.get("width")
                    and candidate.get("height")
                    and candidate["width"] < candidate["height"]):
                best = candidate
                break
        if not best:
            continue
        target = MEDIA_CACHE / f"pixabay-v-{hit.get('id')}.mp4"
        if str(target) in exclude:
            continue
        if _download(best["url"], target, 20000):
            items.append(MediaItem(target, "Pixabay", "video", query, tags,
                                   "Pixabay Content License",
                                   str(hit.get("pageURL") or "")))
        if len(items) >= count:
            break
    return items


def _pixabay_photos(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    key = env("PIXABAY_API_KEY")
    if not key:
        return []
    response = requests.get(
        "https://pixabay.com/api/",
        params={"key": key, "q": query, "image_type": "photo",
                "orientation": "vertical", "per_page": max(20, count * 5),
                "safesearch": "true"},
        timeout=25,
    )
    response.raise_for_status()
    items: list[MediaItem] = []
    for hit in response.json().get("hits", []):
        tags = str(hit.get("tags") or "")
        if not _relevant(tags, query) or not _not_abstract(tags, query):
            continue
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        target = MEDIA_CACHE / f"pixabay-p-{hit.get('id')}.jpg"
        if str(target) in exclude:
            continue
        if _download(url, target, 2048):
            items.append(MediaItem(target, "Pixabay", "image", query, tags,
                                   "Pixabay Content License",
                                   str(hit.get("pageURL") or "")))
        if len(items) >= count:
            break
    return items


def _unsplash(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    key = env("UNSPLASH_ACCESS_KEY") or env("UNSPLASH_API_KEY")
    if not key:
        return []
    response = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "orientation": "portrait",
                "per_page": max(12, count * 5), "content_filter": "high"},
        headers={"Authorization": f"Client-ID {key}"},
        timeout=25,
    )
    response.raise_for_status()
    items: list[MediaItem] = []
    for photo in response.json().get("results", []):
        alt = str(photo.get("alt_description") or photo.get("description") or "")
        if not _relevant(alt, query) or not _not_abstract(alt, query):
            continue
        urls = photo.get("urls") or {}
        url = urls.get("regular") or urls.get("full")
        target = MEDIA_CACHE / f"unsplash-{photo.get('id')}.jpg"
        if str(target) in exclude:
            continue
        if _download(url, target, 2048):
            links = photo.get("links") or {}
            items.append(MediaItem(target, "Unsplash", "image", query, alt,
                                   "Unsplash License",
                                   str(links.get("html") or "")))
        if len(items) >= count:
            break
    return items


def _openverse(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    if _host_blocked(OPENVERSE_API):
        return []
    key = env("OPENVERSE_API_KEY") or env("OPENVERSE_CLIENT_ID")
    headers = dict(UA)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = _get(
        OPENVERSE_API,
        params={"q": query, "page_size": max(15, count * 5),
                "license_type": "all-cc", "mature": "false"},
        headers=headers,
        timeout=25,
    )
    response.raise_for_status()
    items: list[MediaItem] = []
    for item in response.json().get("results", []):
        title = str(item.get("title") or "")
        if not _relevant(title, query) or not _not_abstract(title, query):
            continue
        url = item.get("url") or item.get("thumbnail")
        ident = item.get("id") or _safe_id(title)
        target = MEDIA_CACHE / f"openverse-{ident}.jpg"
        if str(target) in exclude:
            continue
        if _download(url, target, 2048):
            lic = " ".join(str(x) for x in (item.get("license"), item.get("license_version")) if x)
            lic = lic.strip().lower()
            if lic and lic not in {"cc0", "pdm", "public domain"} and not lic.startswith("cc"):
                lic = f"cc {lic}"
            page_url = (item.get("foreign_landing_url") or item.get("detail_url")
                        or item.get("url") or "")
            items.append(MediaItem(target, "Openverse", "image", query, title,
                                   lic or "Creative Commons", str(page_url)))
        if len(items) >= count:
            break
    return items


def _wikimedia(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    """Wikimedia Commons: imágenes y vídeos con licencia libre."""
    if _host_blocked(WIKI_API):
        return []
    response = _get(
        WIKI_API,
        params={
            "action": "query", "format": "json",
            "generator": "search", "gsrsearch": query,
            "gsrnamespace": "6", "gsrlimit": str(max(15, count * 5)),
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "iiurlwidth": "1080",
        },
        headers=UA,
        timeout=30,
    )
    response.raise_for_status()
    pages = (response.json().get("query") or {}).get("pages") or {}
    items: list[MediaItem] = []
    for page in pages.values():
        title = str(page.get("title") or "").replace("File:", "")
        if not _relevant(title, query) or not _not_abstract(title, query):
            continue
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info_item = infos[0]
        mime = str(info_item.get("mime") or "")
        meta = info_item.get("extmetadata") or {}
        licence = _plain((meta.get("LicenseShortName") or {}).get("value") or "")
        # Las categorías de Commons delatan pinturas, grabados y escaneos que
        # el título no menciona («Destruction of Pompeii and Herculaneum»).
        categories = _plain((meta.get("Categories") or {}).get("value") or "")
        check_text = f"{title} {categories}".strip()
        if not _relevant(check_text, query) or not _not_abstract(check_text, query):
            continue
        kind = "video" if mime.startswith("video/") else "image"
        if kind == "video":
            if int(info_item.get("size") or 0) > 70 * 1024 * 1024:
                continue
            url = info_item.get("url")
        else:
            if not (mime.startswith("image/") or not mime):
                continue
            url = info_item.get("thumburl") or info_item.get("url")
        ident = page.get("pageid") or _safe_id(title)
        target = MEDIA_CACHE / f"commons-{ident}{_ext_for(mime)}"
        if str(target) in exclude:
            continue
        if _download(url, target, 2048):
            items.append(MediaItem(target, "Wikimedia Commons", kind, query,
                                   title, licence or "Wikimedia Commons",
                                   str(info_item.get("descriptionurl") or "")))
        if len(items) >= count:
            break
    return items


def _node_exe() -> str | None:
    root = MEDIA_CACHE.parents[1]
    for candidate in (root / "tools" / "node").glob("*/node.exe"):
        if candidate.exists():
            return str(candidate)
    return None


def _yt_meta(path: Path) -> tuple[str, str, str]:
    """Título, descripción y URL que yt-dlp deja junto al vídeo."""
    info = path.with_suffix(".info.json")
    if not info.exists():
        return "", "", ""
    try:
        data = json.loads(info.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return "", "", ""
    if not isinstance(data, dict):
        return "", "", ""
    return (str(data.get("title") or ""),
            str(data.get("description") or "")[:400],
            str(data.get("webpage_url") or ""))


def _yt_ok(path: Path, query: str) -> bool:
    """Descarta fragmentos cuyo título/descripción no hablen de la escena."""
    title, description, _ = _yt_meta(path)
    text = f"{title} {description}".strip()
    if not text:
        return True  # sin metadatos no podemos comprobarlo
    return _relevant(text, query) and _not_abstract(text, query)


def _youtube(cfg: dict, query: str, count: int, exclude: set[str]) -> list[MediaItem]:
    """Busca y descarga SOLO resultados de esta consulta (caché por hash)."""
    budget = int(cfg.get("_yt_budget", 2) or 0) if isinstance(cfg, dict) else 2
    if budget <= 0:
        return []
    if isinstance(cfg, dict):
        cfg["_yt_budget"] = budget - 1

    MEDIA_CACHE.mkdir(parents=True, exist_ok=True)
    qhash = hashlib.md5(query.encode("utf-8")).hexdigest()[:12]
    prefix = MEDIA_CACHE / f"yt-{qhash}-"
    existing = sorted(MEDIA_CACHE.glob(f"yt-{qhash}-*"), key=lambda p: p.stat().st_mtime,
                      reverse=True)
    usable = [p for p in existing if p.suffix.lower() in VIDEO_EXT
              and p.stat().st_size > 100000 and str(p) not in exclude
              and _yt_ok(p, query)]
    if usable:
        title, _, page = _yt_meta(usable[0])
        return [MediaItem(usable[0], "YouTube", "video", query, title,
                          "YouTube (fragmento)", page)][:count]

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-warnings", "--no-playlist", "--no-overwrites",
        "--write-info-json", "--match-filter", "duration < 600",
        "-f", "b[height<=720][ext=mp4]/b[height<=720]",
        "--ffmpeg-location", str(Path(ffmpeg_exe()).parent),
        "-o", str(prefix) + "%(id)s.%(ext)s",
        f"ytsearch{max(1, count * 2)}:{query}",
    ]
    node = _node_exe()
    if node:
        cmd += ["--js-runtimes", f"node:{node}"]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception as exc:
        warn(f"YouTube: {str(exc)[:90]}")
        return []

    files = sorted(MEDIA_CACHE.glob(f"yt-{qhash}-*"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    items: list[MediaItem] = []
    for path in files:
        if path.suffix.lower() not in VIDEO_EXT or path.stat().st_size < 100000:
            continue
        if str(path) in exclude or not _yt_ok(path, query):
            continue
        title, _, page = _yt_meta(path)
        items.append(MediaItem(path, "YouTube", "video", query, title,
                               "YouTube (fragmento)", page))
        if len(items) >= count:
            break
    return items


def _video_sources(index: int) -> list:
    # Openverse solo devuelve imágenes: no se llama para vídeo.
    base = [_pixabay_videos, _pexels_videos, _wikimedia, _youtube]
    shift = index % len(base)
    return base[shift:] + base[:shift]


def _image_sources(index: int) -> list:
    base = [_wikimedia, _unsplash, _pixabay_photos, _pexels_photos, _openverse]
    shift = index % len(base)
    return base[shift:] + base[:shift]


# Palabras que definen el tema del vídeo («pompeii», «earthquake»). Cuando la
# consulta de una escena empieza por una de ellas, se exige además una palabra
# propia de la escena: unas ruinas genéricas no valen para «esqueletos».
_MATCH_CTX: set[str] = set()
_TOPIC_QUERY = ""


def _set_match_context(title: str, scenes: list[dict]) -> None:
    global _MATCH_CTX, _TOPIC_QUERY
    # Solo el título define el tema del vídeo. La intro suele repetir el
    # sujeto de una escena concreta y no debe convertirse en tema global.
    seed = set(_tokens(_translate_query(title)))
    query_sets = [set(_tokens(_translate_query(s.get("query", ""))))
                  | set(_tokens(_translate_query(s.get("subject", ""))))
                  for s in scenes if s.get("query") or s.get("subject")]
    query_sets = [item for item in query_sets if item]
    if not seed or not query_sets:
        _MATCH_CTX = seed
        _TOPIC_QUERY = next(iter(seed), "")
        return
    shared = {word for word in seed
              if sum(word in words for words in query_sets) * 2 >= len(query_sets)}
    if not shared:
        # Sin palabra común a la mayoría: basta con que venga del tema.
        union = set().union(*query_sets)
        shared = {word for word in seed if word in union}
    _MATCH_CTX = shared
    counts = {word: sum(word in words for words in query_sets) for word in shared}
    _TOPIC_QUERY = max(counts, key=counts.get) if counts else ""


def _relevant(text: str, query: str) -> bool:
    return _relevant_base(text, query, topic_tokens=_MATCH_CTX or None)


def _term_hits(text: str, terms: list[str]) -> int:
    """Cuenta términos de la escena presentes en el título del recurso.

    «car factory assembly line Europe» no puede aceptar un «cable car» de los
    Alpes: solo coincide «europe» y el recurso no es una fábrica.
    """
    raw = _plain_norm(text)
    hits = 0
    for term in terms:
        if len(term) >= 5:
            # Raíz estable: «collapsed» debe casar con «collapse» y «assembly»
            # con «assembled».
            pattern = r"(?<![a-z])" + re.escape(term[:-1]) + r"[a-z]*"
        else:
            pattern = r"(?<![a-z])" + re.escape(term) + r"(s|es)?(?![a-z])"
        if re.search(pattern, raw):
            hits += 1
    return hits


def _fill_query(query: str, title: str = "") -> str:
    query = " ".join(str(query or "").split())
    if not query:
        query = _scene_query(title, title) if title else "news"
    # Consulta principal + consulta traducida si venía en español.
    translated = _translate_query(query)
    if translated and translated.lower() != query.lower():
        return translated
    return query


# ------------------------------------------------------------------ montaje
_BLACK_RE = re.compile(r"black_start:([\d.]+) black_end:([\d.]+)")


def _black_windows(src: Path, scan: float) -> list[tuple[float, float]]:
    """Intervalos negros del origen (hasta `scan` segundos)."""
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-nostats", "-t", f"{scan:.2f}",
             "-i", str(src), "-vf", "blackdetect=d=0.12:pix_th=0.10",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180,
        )
    except Exception:
        return []
    return [(float(a), float(b)) for a, b in _BLACK_RE.findall(proc.stderr or "")]


def _safe_start(src: Path, length: float) -> float:
    """Punto de inicio del origen sin fotogramas negros durante `length`.

    Los vídeos de YouTube suelen llevar fundidos/negros en los cortes. Si el
    clip empieza en 0 y el negro cae dentro del tramo usado, el short enseña
    un parpadeo negro. Aquí se busca una ventana limpia; si no existe, se
    mantiene el inicio original.
    """
    windows = _black_windows(Path(src), max(60.0, length * 4))
    if not windows:
        return 0.0
    candidates = [0.0]
    for _, end in windows:
        candidates.append(round(end + 0.06, 3))
    for start, _ in windows:
        if start - length - 0.06 >= 0:
            candidates.append(round(start - length - 0.06, 3))
    for start in sorted(set(candidates)):
        end = start + length
        if not any(start < w_end and end > w_start for w_start, w_end in windows):
            return start
    return 0.0


def _normalize(src: Path, out: Path, length: float, cfg: dict, index: int = 0,
               start_at: float | None = None) -> Path:
    """Convierte cualquier recurso en un clip 1080x1920 con zoom dinámico."""
    width = int(get(cfg, "video.width", 1080))
    height = int(get(cfg, "video.height", 1920))
    fps = int(get(cfg, "video.fps", 30))
    image = Path(src).suffix.lower() in IMAGE_EXT
    if image:
        start = 0.0
    elif start_at is not None and start_at > 0.001:
        # Reutilización de un vídeo propio: empezamos en otro punto para que
        # el plano repetido no se vea idéntico al anterior.
        start = max(0.0, float(start_at))
    else:
        start = _safe_start(Path(src), length + 0.6)
    args = ["-ss", f"{start:.3f}"] if start > 0.001 else []
    args += ["-loop", "1"] if image else ["-stream_loop", "-1"]
    args += ["-i", str(src), "-t", f"{length:.2f}"]
    # Zoom lento alternando entrada/salida para que ningún plano sea estático.
    if index % 2 == 0:
        zoom = f"zoompan=z='min(1.0+0.00055*on,1.09)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
    else:
        zoom = f"zoompan=z='max(1.09-0.00055*on,1.0)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},setsar=1,fps={fps},{zoom},"
          f"format=yuv420p")
    ffmpeg([*args, "-vf", vf, "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "21", str(out)], timeout=900)
    return out


# --------------------------------------------------------------- GTA 6
# El tema GTA 6 exige material del propio juego (tráilers oficiales, capturas
# de Rockstar/IGDB y arte oficial). Una ciudad genérica o un rótulo "neón" no
# sirve: cada escena debe ser GTA 6 de verdad.
_GTA6_RE = re.compile(
    r"\b(?:gta\s*(?:6|vi)|grand\s+theft\s+auto\s*(?:6|vi))\b", re.I)
_GTA6_ASSETS = Path(__file__).resolve().parents[2] / "assets" / "Stiker GTA 6"
_GTA6_POOL: list[MediaItem] | None = None


def is_gta6(text: str) -> bool:
    return bool(_GTA6_RE.search(str(text or "")))


def _gta6_anchor(query: str) -> str:
    query = " ".join(str(query or "").split())
    return query if is_gta6(query) else f"GTA 6 {query}".strip()


def _gta6_image_pool(cfg: dict) -> list[MediaItem]:
    """Arte oficial local + capturas oficiales de IGDB (GTA VI)."""
    global _GTA6_POOL
    if _GTA6_POOL is not None:
        return _GTA6_POOL
    items: list[MediaItem] = []
    seen_paths: set[str] = set()
    logo_paths: list[Path] = []

    if _GTA6_ASSETS.exists():
        art: list[Path] = []
        for path in sorted(_GTA6_ASSETS.glob("*.png")):
            name = path.stem.lower()
            # El arte con personajes va primero; los logos al final para que
            # no sustituyan a una escena del juego.
            (logo_paths if "logo" in name else art).append(path)
        for path in art:
            items.append(MediaItem(
                path, "Rockstar Games", "image", "GTA 6",
                f"Grand Theft Auto VI — {path.stem} (arte oficial)",
                "© Rockstar Games (uso editorial)", ""))
            seen_paths.add(str(path))

    try:
        from .gta_media import igdb_search
        games = igdb_search("Grand Theft Auto VI", limit=3)
    except Exception as exc:  # noqa: BLE001
        warn(f"IGDB GTA 6 no disponible: {str(exc)[:90]}")
        games = []

    urls: list[str] = []
    for game in games:
        # Las capturas van al final de la lista: se invierte para que las
        # imágenes de juego manden sobre carátulas y logos.
        for url in reversed(game.get("images") or []):
            if url and url not in urls:
                urls.append(url)
    for url in urls:
        ident = hashlib.md5(url.encode("utf-8")).hexdigest()[:12]
        target = MEDIA_CACHE / f"gta6-{ident}.jpg"
        if str(target) in seen_paths:
            continue
        if not _download(url, target, 4096):
            continue
        items.append(MediaItem(
            target, "IGDB / Rockstar", "image", "GTA 6",
            "Grand Theft Auto VI — captura oficial",
            "© Rockstar Games (uso editorial)", ""))
        seen_paths.add(str(target))
    for path in logo_paths:
        items.append(MediaItem(
            path, "Rockstar Games", "image", "GTA 6",
            f"Grand Theft Auto VI — {path.stem} (logo oficial)",
            "© Rockstar Games (uso editorial)", ""))
        seen_paths.add(str(path))
    _GTA6_POOL = items
    info(f"GTA 6: {len(items)} imágenes oficiales disponibles")
    return items


def _search_gta6(cfg: dict, query: str, kind: str, exclude: set[str],
                 index: int, video_only: bool = False) -> MediaItem | None:
    """Busca solo material de GTA 6; nunca cae en imágenes genéricas."""
    if kind == "video" or video_only:
        candidates = list(dict.fromkeys([
            _gta6_anchor(query),
            f"{_gta6_anchor(query)} official trailer Rockstar",
            "GTA 6 official trailer Rockstar Games",
            "Grand Theft Auto VI gameplay trailer",
        ]))
        for candidate in candidates:
            try:
                found = _youtube(cfg, candidate, 1, exclude)
            except Exception as exc:  # noqa: BLE001
                warn(f"YouTube GTA 6: {str(exc)[:80]}")
                found = []
            for item in found:
                if is_gta6(item.title) and str(item.path) not in exclude:
                    return item
        if video_only:
            return None

    pool = [item for item in _gta6_image_pool(cfg)
            if str(item.path) not in exclude and item.path.exists()]
    if not pool:
        return None
    return pool[index % len(pool)]


def _first_item(cfg: dict, query: str, kind: str, exclude: set[str],
                index: int, allow_youtube: bool,
                prefer_youtube: bool = False,
                video_only: bool = False) -> MediaItem | None:
    sources = _video_sources(index) if kind == "video" else _image_sources(index)
    if prefer_youtube and kind == "video" and _youtube in sources:
        sources = [_youtube] + [fn for fn in sources if fn is not _youtube]
    if not allow_youtube:
        sources = [fn for fn in sources if fn is not _youtube]
    for fn in sources:
        try:
            items = fn(cfg, query, 1, exclude)
        except Exception as exc:
            warn(f"{fn.__name__}: {str(exc)[:70]}")
            continue
        for item in items:
            if (video_only and item.kind != "video"):
                continue
            if str(item.path) not in exclude and item.path.exists() and item.path.stat().st_size > 1024:
                return item
    return None


def _search_scene(cfg: dict, query: str, kind: str, title: str,
                  exclude: set[str], index: int,
                  prefer_youtube: bool = False,
                  video_only: bool = False,
                  subject: str = "") -> MediaItem | None:
    if is_gta6(title):
        item = _search_gta6(cfg, query, kind, exclude, index, video_only=video_only)
        if item is not None:
            return item
    queries: list[str] = []
    for candidate in (query, f"{query} {title}" if title else "", _scene_query(query),
                      _scene_query(title, title) if title else ""):
        candidate = _fill_query(candidate, title)
        if candidate and candidate.lower() not in {q.lower() for q in queries}:
            queries.append(candidate)

    # El sujeto real de la escena manda: «Donald Trump» + la acción. Así la
    # búsqueda no se queda en un edificio, una multitud o un tribunal genérico.
    subject = " ".join(str(subject or "").split())
    subject_query = _fill_query(subject, title) if subject else ""
    if subject_query:
        # Primero sujeto + acción; después el sujeto solo como respaldo. Si la
        # IA ya incluyó el sujeto en la consulta, no lo duplicamos.
        subject_words = set(_subject_tokens(subject))
        joined = f"{subject_query} {query}".strip()
        if subject_words and subject_words <= set(_tokens(query)):
            joined = query
        for candidate in reversed((joined, subject_query)):
            if candidate and candidate.lower() not in {q.lower() for q in queries}:
                queries.insert(0, candidate)

    # Consultas degradadas: conservan lo específico de la escena y el tema,
    # pero descartan adjetivos como «ancient» que lastran la búsqueda.
    specific: list[str] = []
    for word in _translate_query(query).split():
        if word in _GENERIC_VISUAL or word in _MATCH_CTX:
            continue
        if word not in specific:
            specific.append(word)
    pools: list[list[str]] = []
    if specific:
        # Combinaciones contiguas: «plaster casts», «vesuvius volcano» son
        # mucho más precisas que una palabra suelta.
        for first, second in zip(specific, specific[1:]):
            pools.append([first, second])
        # Si la escena nombra un lugar/volcán, esa es la consulta clave.
        named = [word for word in specific if word in _NAMED]
        if named:
            pools.append(named[:2])
        # Si no, el término más largo suele ser el sustantivo que define la
        # escena («inscription»), no un adjetivo de relleno.
        longest = max(specific, key=len)
        if len(longest) >= 5:
            pools.append([longest])
        for size in (2, 3):
            if len(specific) >= size:
                pools.append(specific[-size:])
    else:
        # La escena solo pide el tema («ruinas de Pompeya»): se busca el
        # tema tal cual y con sus descriptores, en vez de una consulta larga
        # sin palabras útiles.
        pools.append([])
        for word in _translate_query(query).split():
            if word in _MATCH_CTX or word in _NAMED:
                continue
            if word in _GENERIC_VISUAL:
                pools.append([word])
    for pool in pools:
        tail = " ".join(pool)
        candidates = (f"{tail} {_TOPIC_QUERY}".strip(),) if _TOPIC_QUERY else (tail,)
        for candidate in candidates:
            if candidate and candidate.lower() not in {q.lower() for q in queries}:
                queries.append(candidate)

    # Términos propios de la escena (ni tema global, ni descriptores genéricos,
    # ni nombres propios). Se comprueban contra la consulta ORIGINAL: una
    # consulta degradada puede devolver un recurso del tema que no tiene nada
    # que ver con la acción de la escena (un jardín de Pompeya para un muro
    # derrumbado por un terremoto).
    specific = list(dict.fromkeys(
        word for word in _translate_query(query).split()
        if word not in _MATCH_CTX and word not in _GENERIC_VISUAL
        and word not in _NAMED))
    specific_query = " ".join(specific)

    # Nombres propios que la escena original exige. Las consultas degradadas
    # pueden perder alguno («modern naples city mount vesuvius» → «naples
    # mount pompeii»): el recurso debe seguir mencionándolos todos.
    required = [word for word in _tokens(_translate_query(query))
                if word in _NAMED and word not in _MATCH_CTX]

    # Si el sujeto es una persona, el recurso debe nombrarla. Una foto de una
    # multitud, de un tribunal o de un edificio no sirve para una escena sobre
    # Donald Trump: tiene que ser él.
    subject_person = bool(subject) and _person_subject(subject)
    anchor = ""
    if subject_person:
        words = [word for word in _subject_tokens(subject)
                 if word not in {"jr", "sr", "ii", "iii", "iv"}]
        anchor = words[-1] if words else ""

    subject_only = subject_query.strip().lower()
    def attempt() -> MediaItem | None:
        skip = set(exclude)
        for pos, candidate in enumerate(queries):
            item = _first_item(cfg, candidate, kind, skip, index + pos,
                               allow_youtube=(pos == 0 or kind == "video"),
                               prefer_youtube=prefer_youtube and pos == 0,
                               video_only=video_only)
            if not item:
                continue
            # Solo para personas: el respaldo «sujeto solo» no exige los
            # detalles secundarios de la escena (una foto de Trump sigue
            # siendo él aunque el título no mencione el Congreso). Para
            # lugares, el respaldo debe seguir describiendo la escena.
            is_subject_only = subject_person and bool(subject_only) and (
                candidate.strip().lower() == subject_only)
            if (required and not is_subject_only
                    and not _relevant_base(item.title, " ".join(required))):
                skip.add(str(item.path))
                continue
            if (specific_query and not is_subject_only
                    and not _relevant_base(item.title, specific_query)):
                skip.add(str(item.path))
                continue
            # Una sola palabra genérica no basta: el recurso debe cubrir la
            # escena con al menos dos detalles y uno de ellos debe ser
            # distintivo. Así no entra un «cable car» por «car» ni una
            # protesta en Sofía por «Irán».
            if specific and not is_subject_only:
                distinctive = [w for w in specific if w not in _WEAK_VISUAL]
                hits = _term_hits(item.title, specific)
                strong = _term_hits(item.title, distinctive) if distinctive else 0
                topic_hit = bool(_MATCH_CTX) and _term_hits(item.title, list(_MATCH_CTX)) > 0
                # El tema del vídeo solo cuenta si la propia escena lo pide:
                # en un vídeo sobre Trump, cualquier foto suya no sirve para
                # ilustrar aranceles al automóvil.
                topic_in_query = bool(_MATCH_CTX) and _term_hits(query, list(_MATCH_CTX)) > 0
                all_terms = [w for w in _translate_query(query).split()
                             if w not in _MATCH_CTX]
                hits_all = _term_hits(item.title, all_terms)
                if not distinctive:
                    ok = hits_all + (1 if (topic_hit and topic_in_query) else 0) >= 2
                elif topic_hit and topic_in_query:
                    ok = hits + 1 >= 2
                else:
                    ok = hits >= 2 and strong >= 1
                if not ok:
                    skip.add(str(item.path))
                    continue
            if anchor and not _relevant_base(item.title, anchor):
                skip.add(str(item.path))
                continue
            return item
        return None

    # Una sola pasada estricta: bajar la exigencia dejaría entrar recursos
    # genéricos del tema (unas ruinas de Pompeya en una escena de esqueletos).
    return attempt()


def _resource_items(paths: list | None) -> list[MediaItem]:
    """Convierte rutas subidas por el usuario en MediaItem válidos."""
    items: list[MediaItem] = []
    for raw in (paths or []):
        path = Path(raw).expanduser()
        if not path.is_file():
            warn(f"Recurso propio: no encuentro «{path.name}»; lo salto.")
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_EXT:
            kind = "image"
        elif suffix in VIDEO_EXT:
            kind = "video"
        else:
            warn(f"Recurso propio: «{path.name}» no es una imagen o vídeo compatible.")
            continue
        items.append(MediaItem(
            path=path,
            source="Tus recursos",
            kind=kind,
            query="recurso propio",
            title=path.name,
            license="Archivo propio",
            page="",
        ))
    return items


def _resource_plan(items: list[MediaItem], normalized: list[dict],
                   intro_seconds: float) -> list[dict]:
    """Reparte los recursos propios por la intro y las escenas, en orden.

    Con menos recursos que escenas se repiten en ciclo. Con más, cada escena
    se divide en varios planos para aprovecharlos todos sin romper el corte
    que marca la voz.
    """
    if not items:
        return []
    entries: list[dict] = []
    start = 0
    if intro_seconds > 0:
        entries.append({"item": items[0], "weight": 0.0, "intro": True,
                        "query": "recurso propio"})
        start = 1
    remaining = items[start:] or items
    if not normalized:
        return entries

    weights = []
    for scene in normalized:
        text = str(scene.get("text") or "")
        weights.append(max(0.1, float(scene.get("timing")
                                      or max(1, len(text.split())))))

    # Menos (o igual) recursos que escenas: uno por escena, ciclando.
    if len(remaining) <= len(normalized):
        for index, scene in enumerate(normalized):
            entries.append({
                "item": remaining[index % len(remaining)],
                "weight": weights[index],
                "intro": False,
                "query": "recurso propio",
            })
        return entries

    # Más recursos que escenas: reparto uniforme; cada escena conserva su
    # tramo de narración y dentro de ella los planos duran lo mismo.
    base, extra = divmod(len(remaining), len(normalized))
    cursor = 0
    for index, scene in enumerate(normalized):
        count = max(1, base + (1 if index < extra else 0))
        for _ in range(count):
            if cursor >= len(remaining):
                cursor = 0
            entries.append({
                "item": remaining[cursor],
                "weight": weights[index] / count,
                "intro": False,
                "query": "recurso propio",
            })
            cursor += 1
    return entries


def prepare_backgrounds(cfg: dict, duration: float, work: Path,
                        scenes: list | None = None,
                        intro_query: str | None = None,
                        intro_seconds: float = 0.0,
                        scene_timings: list[float] | None = None,
                        resources: list | None = None) -> list[Path]:
    """Devuelve clips 1080x1920 en `work`, uno por escena, con duración ajustada.

    También deja en `cfg["_segments"]` la duración visible de cada clip y en
    `cfg["_clip_lengths"]` la duración real (con margen para transiciones).
    No hay fallback a fondos negros ni degradados: si una escena no encuentra
    recurso, su tiempo se suma al clip vecino (nunca se genera un fondo falso).
    Si `resources` trae archivos propios, se usan solo esos y no se busca en
    internet.
    """
    global _MATCH_CTX
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    title = str(cfg.get("_title", "") or "")
    duration = max(1.0, float(duration))
    intro_seconds = max(0.0, min(float(intro_seconds or 0.0), max(0.0, duration - 2.0)))
    narration = max(0.5, duration - intro_seconds)
    transition = float(get(cfg, "video.transition_seconds", 0.45) or 0.45)

    # Normalizamos las escenas a dicts con su consulta visual.
    normalized: list[dict] = []
    for scene in (scenes or []):
        if isinstance(scene, dict):
            text = str(scene.get("narration") or scene.get("text") or "")
            query = str(scene.get("visual_query") or scene.get("query") or "")
            kind = str(scene.get("media_type") or "auto").lower()
            subject = str(scene.get("subject") or "").strip()
        else:
            text = str(getattr(scene, "narration", "") or "")
            query = str(getattr(scene, "visual_query", "") or "")
            kind = str(getattr(scene, "media_type", "auto") or "auto").lower()
            subject = str(getattr(scene, "subject", "") or "").strip()
        if not text.strip() and not query.strip():
            continue
        if not subject:
            subject = _main_subject(text)
        query = _fill_query(query or _scene_query(text, title), title)
        if kind not in {"video", "image", "auto"}:
            kind = "auto"
        normalized.append({"query": query, "text": text, "kind": kind,
                           "subject": subject})
    if not normalized:
        normalized = [{"query": _fill_query(str(get(cfg, "media.query", "")), title),
                       "text": title, "kind": "video"}]

    # Palabras del tema: permiten exigir un detalle propio de cada escena y
    # evitar que una imagen genérica del tema sustituya a la escena concreta.
    intro_label = _fill_query(intro_query or _scene_query(title, title), title)
    intro_subject = _main_subject(title)
    _set_match_context(title, normalized)

    # Si Whisper ya midió cada escena, usamos esos segundos como peso real.
    # Así el plano cambia justo cuando la voz cambia de tema.
    exact = False
    if scene_timings and len(scene_timings) == len(normalized):
        timings = [max(0.4, float(value or 0.0)) for value in scene_timings]
        if sum(timings) > 0.5:
            for scene, value in zip(normalized, timings):
                scene["timing"] = value
            exact = True

    # Plan: intro + escenas. La intro usa material del acontecimiento real.
    used: set[str] = set()
    chosen: list[dict] = []

    resource_items = _resource_items(resources)
    if resource_items:
        # Los archivos del usuario mandan: no se busca nada en internet.
        chosen = _resource_plan(resource_items, normalized, intro_seconds)
        used = {str(entry["item"].path) for entry in chosen if entry["item"]}
        info(f"Usando {len(resource_items)} recurso(s) propio(s) en el montaje.")
    else:
        if intro_seconds > 0:
            # La intro debe ser un fragmento real del acontecimiento: primero
            # vídeo (YouTube/stock) y, si no existe, una foto real del suceso.
            item = _search_scene(cfg, intro_label, "video", title, used, 0,
                                 prefer_youtube=True, video_only=True,
                                 subject=intro_subject)
            if not item:
                item = _search_scene(cfg, intro_label, "video", title, used, 0,
                                     prefer_youtube=True, subject=intro_subject)
            if item:
                used.add(str(item.path))
                chosen.append({"item": item, "weight": 0.0, "intro": True,
                               "query": intro_label})
            else:
                warn("Intro: no encontré fragmento real del acontecimiento; se usará el primer clip relacionado.")
                chosen.append({"item": None, "weight": 0.0, "intro": True,
                               "query": intro_label})

        for index, scene in enumerate(normalized):
            kind = scene["kind"]
            if kind == "auto":
                kind = "video" if index % 2 == 0 else "image"
            item = _search_scene(cfg, scene["query"], kind, title, used, index + 1,
                                 subject=scene.get("subject", ""))
            if item:
                used.add(str(item.path))
            else:
                # Segundo intento: el tipo contrario y consulta más amplia.
                other = "image" if kind == "video" else "video"
                item = _search_scene(cfg, scene["query"], other, title, used,
                                     index + 5, subject=scene.get("subject", ""))
                if item:
                    used.add(str(item.path))
            if item:
                chosen.append({"item": item, "weight": scene.get("timing")
                               or max(1, len(scene["text"].split())),
                               "intro": False, "query": scene["query"]})
            else:
                warn(f"Escena {index + 1}: sin recurso real para «{scene['query'][:50]}».")
                chosen.append({"item": None, "weight": scene.get("timing")
                               or max(1, len(scene["text"].split())),
                               "intro": False, "query": scene["query"]})

    # Si la intro no encontró material, reutilizamos el clip más lejano (el
    # último de la lista) para que la repetición no quede pegada a la escena 1
    # ni a la escena 2: sigue siendo material real del tema, no un fondo
    # artificial, y el plano repetido aparece casi al final del vídeo.
    if intro_seconds > 0 and chosen and chosen[0]["item"] is None:
        donors = [c for c in chosen[1:] if c["item"]]
        donor = donors[-1] if donors else None
        if donor:
            chosen[0]["item"] = donor["item"]
        else:
            chosen = chosen[1:]
            intro_seconds = 0.0
            narration = duration

    # Reparte el tiempo de las escenas sin recurso entre la escena vecina.
    merged: list[dict] = []
    pending = 0.0
    for entry in chosen:
        if entry["intro"]:
            merged.append(entry)
            continue
        if entry["item"] is None:
            # El tiempo de una escena sin recurso pasa al siguiente plano real.
            pending += float(entry["weight"] or 0.0)
            continue
        if pending:
            entry["weight"] = float(entry["weight"] or 0.0) + pending
            pending = 0.0
        merged.append(entry)
    if pending:
        target = next((e for e in reversed(merged) if not e["intro"]), None)
        if target:
            target["weight"] = float(target["weight"] or 0.0) + pending
    if intro_seconds > 0 and merged and merged[0]["intro"] and merged[0]["item"] is None:
        merged.pop(0)
    real = [e for e in merged if e["item"] is not None]
    if not real:
        raise RuntimeError(
            "No se encontró ningún recurso visual real. Revisa las claves de "
            "Pixabay/Pexels/Unsplash y la conexión, o cambia el tema."
        )

    # Duraciones: intro fija y resto proporcional al peso de cada escena
    # (segundos reales de Whisper si existen; si no, número de palabras).
    scene_entries = [e for e in real if not e["intro"]]
    total_weight = sum(float(e["weight"] or 0.0) for e in scene_entries) or 1.0
    segments: list[float] = []
    if intro_seconds > 0 and real[0]["intro"]:
        segments.append(intro_seconds)
    for entry in scene_entries:
        segments.append(narration * float(entry["weight"] or 0.0) / total_weight)
    # Con tiempos reales no aplicamos el mínimo artificial: el corte ya está
    # alineado con la voz. Con recursos propios tampoco: el reparto en varios
    # planos por escena es deliberado y estirar tramos rompería ese plan.
    if not exact and not resource_items and len(segments) > (1 if intro_seconds > 0 else 0) + 1:
        floor = 1.8
        for _ in range(8):
            short = [i for i, s in enumerate(segments)
                     if s < floor and not (intro_seconds > 0 and i == 0)]
            if not short:
                break
            i = short[0]
            donor_i = i - 1 if i > 0 and not (intro_seconds > 0 and i - 1 == 0) else i + 1
            if donor_i < 0 or donor_i >= len(segments):
                break
            take = min(floor - segments[i], max(0.0, segments[donor_i] - floor))
            if take <= 0:
                segments[i] = floor
                break
            segments[i] += take
            segments[donor_i] -= take
    total = sum(segments)
    if total > 0 and abs(total - duration) > 0.01:
        segments[-1] += duration - total

    outputs: list[Path] = []
    credits: list[dict] = []
    # Si un vídeo propio se reutiliza, cada aparición empieza en un punto
    # distinto para que el plano no se vea repetido.
    planned: dict[str, int] = {}
    seen: dict[str, int] = {}
    for entry in real:
        key = str(entry["item"].path)
        planned[key] = planned.get(key, 0) + 1
    durations: dict[str, float] = {}

    for index, (entry, segment) in enumerate(zip(real, segments)):
        item: MediaItem = entry["item"]
        out = work / f"bg{index}.mp4"
        start_at = 0.0
        key = str(item.path)
        used_before = seen.get(key, 0)
        seen[key] = used_before + 1
        if item.kind == "video" and used_before > 0 and planned.get(key, 1) > 1:
            if key not in durations:
                durations[key] = probe_duration(item.path)
            total_dur = durations[key]
            span = total_dur - (segment + transition) - 1.0
            if span > 0.5:
                start_at = span * used_before / planned[key]
        try:
            _normalize(item.path, out, segment + transition, cfg, index=index,
                       start_at=start_at)
        except Exception as exc:
            warn(f"No pude preparar el clip {index + 1} ({item.source}): {str(exc)[:100]}")
            continue
        outputs.append(out)
        credits.append({
            "file": out.name,
            "source": item.source,
            "title": item.title,
            "license": item.license,
            "page": item.page,
            "query": entry["query"],
            "seconds": round(segment, 2),
        })
        info(f"Escena {index + 1}: {item.source} · {segment:.1f}s · «{entry['query'][:48]}»")

    if not outputs:
        if resource_items:
            raise RuntimeError(
                "No pude convertir los recursos que subiste en clips. "
                "Comprueba que los vídeos se abren y que las imágenes no están dañadas."
            )
        raise RuntimeError("Los recursos encontrados no se pudieron convertir en clips.")
    # Reajusta los segmentos a los clips que realmente se generaron.
    if len(outputs) != len(segments):
        segments = segments[:len(outputs)]
    cfg["_segments"] = segments
    cfg["_clip_lengths"] = [s + transition for s in segments]
    cfg["_transition_seconds"] = transition
    try:
        write_json(work / "media_credits.json", credits)
    except Exception:
        pass
    _MATCH_CTX = set()
    return outputs
