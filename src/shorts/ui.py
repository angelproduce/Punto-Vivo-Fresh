"""Interfaz web local: moderna, sin dependencias extra y sin salir de tu PC.

Arranca con:   .\\run.cmd ui          (o el acceso directo ANGEL PRODUCE IA.exe)

Expone una API JSON muy pequeña y sirve la carpeta ./web. Todo escucha solo en
127.0.0.1: nadie de fuera puede entrar.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from . import __version__, graphics, oauth, queue, script_gen, tts
from .config import env, get, load_config, path_of
from .frames import FRAME_DIR, FRAMES as FRAME_TEMPLATES, frame_choices
from .util import (APPROVED, IMAGE_EXT, LOGS, OUTPUT, PENDING, PUBLISHED, ROOT,
                   STATE, VIDEO_EXT, WORK, disk_free_gb, ensure_dirs,
                   probe_duration, read_json, setup_console, slugify,
                   write_json)

WEB = Path(__file__).resolve().parent / "web"
RESOURCE_DIR = STATE / "resources"
MAX_RESOURCE_BYTES = 300 * 1024 * 1024  # 300 MB por archivo
MAX_RESOURCES = 20
_RESOURCE_DURATIONS: dict[tuple[str, int], float] = {}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STEP_RE = re.compile(r"▸\s*(\d)/6\s*·\s*(.+)")
TOTAL_RE = re.compile(r"Tiempo total:\s*(\d+)s")
UI_STATE = STATE / "ui.json"
STATIC = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.css": "app.css",
    "/app.js": "app.js",
}
MAX_BODY = 64 * 1024 * 1024  # 64 MB (audio de referencia)


def _clean(line: str) -> str:
    return ANSI_RE.sub("", line).replace("\r", "").rstrip()


# ------------------------------------------------------------------ trabajos

class Job:
    """Un proceso del pipeline a la vez, con su salida en vivo."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._reset()

    def _reset(self) -> None:
        self.running = False
        self.kind = ""
        self.label = ""
        self.step = 0
        self.step_name = ""
        self.lines: list[str] = []
        self.started = 0.0
        self.finished = 0.0
        self.ok: bool | None = None
        self.error = ""
        self.result = ""
        self.estimate = 0.0

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = (time.time() - self.started) if self.started else 0.0
            if self.running:
                percent = 0.04
                if self.estimate > 0:
                    percent = min(0.965, elapsed / self.estimate)
                else:
                    percent = min(0.9, elapsed / 420)
            elif self.ok:
                percent = 1.0
            elif self.started:
                percent = min(0.99, self.step / 6 if self.step else 0.5)
            else:
                percent = 0.0
            return {
                "running": self.running,
                "kind": self.kind,
                "label": self.label,
                "step": self.step,
                "step_name": self.step_name,
                "percent": round(percent * 100, 1),
                "elapsed": round(elapsed, 1),
                "started": self.started,
                "finished": self.finished,
                "ok": self.ok,
                "error": self.error,
                "result": self.result,
                "estimate": self.estimate,
                "lines": self.lines[-140:],
            }

    # -- ciclo de vida ----------------------------------------------------

    def start(self, kind: str, args: list[str], label: str, estimate: float = 420.0) -> bool:
        with self._lock:
            if self.running:
                return False
            self._reset()
            self.running = True
            self.kind = kind
            self.label = label
            self.started = time.time()
            self.estimate = estimate
            self.step = 1 if kind == "run" else 0
            self.step_name = "Preparando" if kind == "run" else ""

        env_vars = os.environ.copy()
        env_vars["HF_HOME"] = str(ROOT / ".cache" / "huggingface")
        env_vars["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        env_vars["PYTHONIOENCODING"] = "utf-8"
        env_vars["PYTHONUNBUFFERED"] = "1"
        env_vars.setdefault("TOKENIZERS_PARALLELISM", "false")

        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "shorts", *args],
                cwd=str(ROOT),
                env=env_vars,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except Exception as exc:  # pragma: no cover - arranque imposible
            with self._lock:
                self.running = False
                self.ok = False
                self.error = str(exc)
                self.finished = time.time()
            return True

        self._proc = proc
        threading.Thread(target=self._pump, args=(proc,), daemon=True).start()
        return True

    def _pump(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _clean(raw)
            if not line.strip():
                continue
            with self._lock:
                self.lines.append(line)
                if len(self.lines) > 500:
                    del self.lines[:200]
                match = STEP_RE.search(line)
                if match:
                    self.step = int(match.group(1))
                    self.step_name = match.group(2).strip()
                total = TOTAL_RE.search(line)
                if total:
                    self.result = line.strip()
            if line.strip().startswith("✗"):
                with self._lock:
                    self.error = line.strip()

        code = proc.wait()
        with self._lock:
            self.running = False
            self.finished = time.time()
            self.ok = code == 0 and not self.error
            if not self.ok and not self.error:
                tail = [l for l in self.lines if l.strip()][-3:]
                self.error = tail[-1].strip() if tail else f"El proceso terminó con código {code}"
            elapsed = self.finished - self.started
        if self.ok and self.kind == "run":
            _learn_duration(elapsed)

    def cancel(self) -> bool:
        with self._lock:
            proc = self._proc
            if not self.running or proc is None:
                return False
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:  # pragma: no cover
            proc.terminate()
        with self._lock:
            self.running = False
            self.ok = False
            self.error = "Cancelado por ti."
            self.finished = time.time()
        return True


def _learn_duration(seconds: float) -> None:
    data = read_json(UI_STATE, {}) or {}
    runs = [float(x) for x in (data.get("runs") or []) if isinstance(x, (int, float))]
    runs = (runs + [round(seconds, 1)])[-7:]
    runs.sort()
    data["runs"] = runs
    data["last_run"] = round(seconds, 1)
    write_json(UI_STATE, data)


def _estimate_seconds() -> float:
    data = read_json(UI_STATE, {}) or {}
    runs = sorted(float(x) for x in (data.get("runs") or []) if isinstance(x, (int, float)))
    if not runs:
        return 420.0
    return max(90.0, runs[len(runs) // 2])


def _manual_script_file(text: str) -> Path:
    """Guarda el guion pegado en la UI para pasárselo al pipeline."""
    folder = ROOT / ".cache" / "ui"
    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.glob("script-*.txt"):
        try:
            if now - old.stat().st_mtime > 7 * 24 * 3600:
                old.unlink()
        except OSError:
            pass
    path = folder / f"script-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _run_sources(data: dict) -> list[str]:
    raw = data.get("sources")
    if isinstance(raw, str):
        raw = re.split(r"[\s,]+", raw)
    out: list[str] = []
    for item in raw or []:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text[:2048])
    return out[:5]


JOB = Job()


# ------------------------------------------------------------------ estado

def _topic_list(cfg: dict) -> dict:
    path = path_of(get(cfg, "script.topics_file", "topics.yaml"))
    items: list[dict] = []
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        used = set(read_json(ROOT / "state" / "used_topics.json", []) or [])
        for topic in data.get("topics") or []:
            items.append({
                "id": str(topic.get("id", "")),
                "title": topic.get("title", ""),
                "used": str(topic.get("id", "")) in used,
            })
    except Exception:
        pass
    return {
        "total": len(items),
        "available": len([i for i in items if not i["used"]]),
        "items": items,
    }


def _queue_item(meta: dict, status: str) -> dict:
    folder = Path(meta.get("dir", ""))
    cover = folder / "cover.jpg"
    video = folder / "video.mp4"
    return {
        "id": meta.get("id", folder.name),
        "title": meta.get("title", "Sin título"),
        "description": meta.get("description", ""),
        "hashtags": meta.get("hashtags") or [],
        "hook": meta.get("hook", ""),
        "text": meta.get("text", ""),
        "duration": round(float(meta.get("duration") or 0), 1),
        "status": status,
        "created_at": meta.get("created_at", ""),
        "published": meta.get("published") or {},
        "cover_url": f"/media?rel={_rel(cover)}" if cover.exists() else "",
        "video_url": f"/media?rel={_rel(video)}" if video.exists() else "",
        "folder": str(folder),
    }


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except Exception:
        return ""


def _platform_state(cfg: dict) -> list[dict]:
    out: list[dict] = []
    for platform in get(cfg, "publish.platforms", []) or []:
        if platform == "youtube":
            token = path_of(get(cfg, "publish.youtube.token", ""))
            secret = path_of(get(cfg, "publish.youtube.client_secret", ""))
            if token.exists():
                out.append({"id": "youtube", "name": "YouTube", "state": "ready", "hint": "Conectado"})
            elif secret.exists():
                out.append({"id": "youtube", "name": "YouTube", "state": "auth", "hint": "Falta autorizar"})
            else:
                out.append({"id": "youtube", "name": "YouTube", "state": "setup",
                            "hint": "Falta client_secret.json"})
        elif platform == "tiktok":
            token = path_of(get(cfg, "publish.tiktok.token", ""))
            if token.exists():
                out.append({"id": "tiktok", "name": "TikTok", "state": "ready", "hint": "Conectado"})
            else:
                out.append({"id": "tiktok", "name": "TikTok", "state": "auth",
                            "hint": "Falta autorizar"})
        elif platform == "facebook":
            if env("FACEBOOK_PAGE_TOKEN"):
                out.append({"id": "facebook", "name": "Facebook", "state": "ready", "hint": "Token listo"})
            else:
                out.append({"id": "facebook", "name": "Facebook", "state": "setup",
                            "hint": "Falta FACEBOOK_PAGE_TOKEN"})
    return out


def settings_payload() -> dict:
    from .config import env
    keys = {
        "GEMINI_API_KEY": env("GEMINI_API_KEY"),
        "OPENAI_API_KEY": env("OPENAI_API_KEY"),
        "GITHUB_TOKEN": env("GITHUB_TOKEN") or env("GITHUB_MODELS_TOKEN"),
        "PEXELS_API_KEY": env("PEXELS_API_KEY"),
        "PIXABAY_API_KEY": env("PIXABAY_API_KEY"),
        "UNSPLASH_ACCESS_KEY": env("UNSPLASH_ACCESS_KEY"),
        "OPENVERSE_API_KEY": env("OPENVERSE_API_KEY"),
        "FACEBOOK_PAGE_TOKEN": env("FACEBOOK_PAGE_TOKEN"),
        "ELEVENLABS_API_KEY": env("ELEVENLABS_API_KEY"),
        "ZERNIO_API_KEY": env("ZERNIO_API_KEY"),
        "NEWSAPI_KEY": env("NEWSAPI_KEY") or env("NEWS_API_KEY"),
        "IGDB_CLIENT_ID": env("IGDB_CLIENT_ID") or env("TWITCH_CLIENT_ID"),
        "IGDB_CLIENT_SECRET": env("IGDB_CLIENT_SECRET") or env("TWITCH_CLIENT_SECRET"),
        "YOUTUBE_API_KEY": env("YOUTUBE_API_KEY") or env("GOOGLE_API_KEY"),
    }
    return {
        "keys": {name: bool(value) for name, value in keys.items()},
        "providers": {
            "script": get(load_config(), "script.provider", "auto"),
            "voice": get(load_config(), "voice.provider", "auto"),
        },
        "voice_tone": get(load_config(), "voice.tone", "breaking"),
        "voice_intensity": int(round((float(get(load_config(), "voice.speed", 1.0) or 1.0) - 1.0) * 100)),
        "media_source": get(load_config(), "media.source", "mixed"),
        "music_db": get(load_config(), "video.music_db", 3),
        "music_intro_db": get(load_config(), "video.music_intro_db", 1.5),
        "music_intro_fade": get(load_config(), "video.music_intro_fade", 0.7),
        "music_fade_in": get(load_config(), "video.music_fade_in", 0.3),
        "frame_template": get(load_config(), "video.frame_template", "") or "",
        "frames": frame_choices(),
        "target_duration": get(load_config(), "project.target_duration", 90),
        "research": bool(get(load_config(), "script.research", True)),
    }

def save_keys(data: dict) -> list[str]:
    import os
    from .config import ROOT
    allowed = {
        "GEMINI_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN",
        "GITHUB_MODELS_TOKEN", "PEXELS_API_KEY", "PIXABAY_API_KEY",
        "UNSPLASH_ACCESS_KEY", "FACEBOOK_PAGE_TOKEN",
        "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
        "ZERNIO_API_KEY",
        "OPENVERSE_API_KEY", "OPENVERSE_CLIENT_ID",
        "NEWSAPI_KEY", "NEWS_API_KEY",
        "IGDB_CLIENT_ID", "TWITCH_CLIENT_ID",
        "IGDB_CLIENT_SECRET", "TWITCH_CLIENT_SECRET",
        "YOUTUBE_API_KEY", "GOOGLE_API_KEY",
    }
    env_path = ROOT / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    existing: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        existing[key] = index

    saved: list[str] = []
    for key, value in (data or {}).items():
        if key not in allowed:
            continue
        value = str(value or "").strip()
        if not value:
            continue
        if key in existing:
            lines[existing[key]] = f"{key}={value}"
        else:
            lines.append(f"{key}={value}")
        os.environ[key] = value
        saved.append(key)
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return saved


def _save_voice_settings(provider: str, edge_voice: str = "") -> None:
    from .config import ROOT
    path = ROOT / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    in_voice = False
    out = []
    for line in lines:
        if line.strip() == "voice:":
            in_voice = True
            out.append(line)
            continue
        if in_voice and (line and not line.startswith(" ") and not line.startswith("\t")):
            in_voice = False
        if in_voice and line.strip().startswith("provider:"):
            out.append('  provider: "' + provider + '"')
            continue
        if in_voice and edge_voice and line.strip().startswith("edge_voice:"):
            out.append('  edge_voice: "' + edge_voice + '"')
            continue
        out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")


def _save_script_provider(provider: str) -> None:
    from .config import ROOT
    path = ROOT / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    in_script = False
    out = []
    for line in lines:
        if line.strip() == "script:":
            in_script = True
            out.append(line)
            continue
        if in_script and (line and not line.startswith(" ") and not line.startswith("\t")):
            in_script = False
        if in_script and line.strip().startswith("provider:"):
            out.append('  provider: "' + provider + '"')
            continue
        if in_script and line.strip().startswith("provider_order:"):
            # ChatGPT primero cuando se elige openai; resto como respaldo.
            order = ["openai", "gemini", "github"] if provider == "openai" else (
                ["gemini", "openai", "github"] if provider == "gemini" else
                ["openai", "gemini", "github"])
            out.append("  provider_order: [" + ", ".join(f'"{n}"' for n in order) + "]")
            continue
        out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")


def _save_media_source(source: str) -> None:
    from .config import ROOT
    path = ROOT / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    in_media = False
    out = []
    for line in lines:
        if line.strip() == "media:":
            in_media = True
            out.append(line)
            continue
        if in_media and (line and not line.startswith(" ") and not line.startswith("\t")):
            in_media = False
        if in_media and line.strip().startswith("source:"):
            out.append('  source: "' + source + '"')
            continue
        out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")


_MUSIC_LIMITS = {
    "music_db": (-20.0, 12.0),
    "music_intro_db": (0.0, 12.0),
    "music_intro_fade": (0.2, 3.0),
    "music_fade_in": (0.0, 1.5),
}

_MUSIC_COMMENTS = {
    "music_db": "ganancia final sobre la música normalizada (UI: -20 a +12)",
    "music_intro_db": "realce muy leve en los primeros segundos (profesional)",
    "music_intro_fade": "rampa de bajada al empezar la narración",
    "music_fade_in": "fundido de entrada para que no golpee",
}


def _fmt_music(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _write_video_values(rendered: dict[str, str], comments: dict[str, str]) -> None:
    """Escribe claves dentro del bloque `video:` de config.yaml.

    `rendered` trae el valor ya formateado para YAML (número o cadena entre
    comillas) y `comments` el comentario que se añade si la clave no existía.
    Se conservan el orden y los comentarios del resto del archivo.
    """
    if not rendered:
        raise ValueError("No hay ajustes que guardar.")
    from .config import ROOT
    path = ROOT / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_video = False
    seen: set[str] = set()

    def flush_missing() -> None:
        for key, text in rendered.items():
            if key not in seen:
                comment = comments.get(key, "")
                tail = f"  # {comment}" if comment else ""
                out.append(f"  {key}: {text}{tail}")
                seen.add(key)

    for line in lines:
        if line.strip() == "video:":
            in_video = True
            out.append(line)
            continue
        if in_video and line and not line.startswith(" ") and not line.startswith("\t"):
            flush_missing()
            in_video = False
        key = line.strip().split(":", 1)[0].strip() if ":" in line else ""
        if in_video and key in rendered:
            indent = line[: len(line) - len(line.lstrip())]
            comment = ""
            if "#" in line:
                comment = "  # " + line.split("#", 1)[1].strip()
            out.append(f"{indent}{key}: {rendered[key]}{comment}")
            seen.add(key)
            continue
        out.append(line)
    if in_video:
        flush_missing()
    path.write_text("\n".join(out), encoding="utf-8")


def _save_music_settings(values: dict) -> None:
    clean = {}
    for key, (lo, hi) in _MUSIC_LIMITS.items():
        if key in values and values[key] is not None:
            try:
                clean[key] = max(lo, min(hi, float(values[key])))
            except (TypeError, ValueError):
                continue
    if not clean:
        raise ValueError("No hay ajustes de música válidos.")
    rendered = {key: _fmt_music(clean[key]) for key in clean}
    _write_video_values(rendered, _MUSIC_COMMENTS)


_FRAME_COMMENT = 'marco decorativo elegido en Ajustes: "gta6", "alofoke3", "angelproduce" o vacío'


def _save_frame_template(name: str) -> None:
    from .frames import normalize

    key = normalize(name)
    if name and not key:
        raise ValueError("Ese marco no existe.")
    _write_video_values({"frame_template": json.dumps(key)},
                        {"frame_template": _FRAME_COMMENT})



def _save_voice_intensity(percent: int) -> None:
    from .config import ROOT
    percent = max(-10, min(40, int(percent)))
    speed = round(1.0 + percent / 100.0, 2)
    if percent >= 0:
        edge_rate = f"+{percent}%"
    else:
        edge_rate = f"{percent}%"
    path = ROOT / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    in_voice = False
    out = []
    for line in lines:
        if line.strip() == "voice:":
            in_voice = True
            out.append(line)
            continue
        if in_voice and (line and not line.startswith(" ") and not line.startswith("\t")):
            in_voice = False
        if in_voice and line.strip().startswith("speed:"):
            out.append(f"  speed: {speed}")
            continue
        if in_voice and line.strip().startswith("edge_rate:"):
            out.append(f'  edge_rate: "{edge_rate}"')
            continue
        out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")


def _save_voice_tone(tone: str) -> None:
    """Guarda el tono de voz y ajusta speed/pause/rate en config.yaml."""
    from .config import ROOT
    presets = {
        "breaking": {"speed": 1.18, "pause_ms": 150, "edge_rate": "+18%", "edge_pitch": "+4Hz"},
        "narrative": {"speed": 0.92, "pause_ms": 380, "edge_rate": "-8%", "edge_pitch": "+0Hz"},
        "calm": {"speed": 0.88, "pause_ms": 500, "edge_rate": "-12%", "edge_pitch": "-2Hz"},
        "motivational": {"speed": 1.05, "pause_ms": 250, "edge_rate": "+5%", "edge_pitch": "+2Hz"},
    }
    p = presets.get(tone, presets["narrative"])
    path = ROOT / "config.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    in_voice = False
    out = []
    for line in lines:
        if line.strip() == "voice:":
            in_voice = True
            out.append(line)
            continue
        if in_voice and (line and not line.startswith(" ") and not line.startswith("\t")):
            in_voice = False
        if in_voice and line.strip().startswith("speed:"):
            out.append(f'  speed: {p["speed"]}')
            continue
        if in_voice and line.strip().startswith("pause_ms:"):
            out.append(f'  pause_ms: {p["pause_ms"]}')
            continue
        if in_voice and line.strip().startswith("edge_rate:"):
            out.append(f'  edge_rate: "{p["edge_rate"]}"')
            continue
        if in_voice and line.strip().startswith("edge_pitch:"):
            out.append(f'  edge_pitch: "{p["edge_pitch"]}"')
            continue
        if in_voice and line.strip().startswith("tone:"):
            out.append(f'  tone: "{tone}"')
            continue
        out.append(line)
    path.write_text("\n".join(out), encoding="utf-8")


def _resource_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXT:
        return "image"
    if suffix in VIDEO_EXT:
        return "video"
    return ""


def _resource_item(path: Path) -> dict:
    """Ficha de un archivo propio lista para la interfaz."""
    stat = path.stat()
    kind = _resource_kind(path)
    duration = 0.0
    if kind == "video":
        key = (path.name, stat.st_mtime_ns)
        if key not in _RESOURCE_DURATIONS:
            try:
                _RESOURCE_DURATIONS[key] = round(probe_duration(path), 1)
            except Exception:
                _RESOURCE_DURATIONS[key] = 0.0
        duration = _RESOURCE_DURATIONS[key]
    return {
        "name": path.name,
        "kind": kind,
        "size": stat.st_size,
        "duration": duration,
        "updated": stat.st_mtime,
        "url": f"/api/resources/file?name={quote(path.name)}&v={int(stat.st_mtime)}",
    }


def _resource_list() -> list[dict]:
    RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
    files = [p for p in RESOURCE_DIR.iterdir()
             if p.is_file() and _resource_kind(p)]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [_resource_item(p) for p in files[:MAX_RESOURCES]]


def _resource_path(name: str) -> Path | None:
    """Convierte un nombre enviado por el navegador en una ruta segura."""
    clean = Path(str(name or "")).name
    if not clean or clean != str(name or "").strip():
        return None
    target = (RESOURCE_DIR / clean).resolve()
    try:
        target.relative_to(RESOURCE_DIR.resolve())
    except ValueError:
        return None
    if not target.is_file() or not _resource_kind(target):
        return None
    return target


def _unique_resource_path(filename: str) -> Path:
    suffix = Path(filename).suffix.lower()
    stem = slugify(Path(filename).stem, limit=60) or "recurso"
    candidate = RESOURCE_DIR / f"{stem}{suffix}"
    index = 2
    while candidate.exists():
        candidate = RESOURCE_DIR / f"{stem}-{index}{suffix}"
        index += 1
    return candidate


def build_state() -> dict:
    cfg = load_config()
    ref = path_of(get(cfg, "voice.ref_audio", "assets/voice/reference.wav"))
    voice = {"exists": ref.exists(), "duration": 0.0, "url": "", "updated": 0.0}
    if ref.exists():
        try:
            voice["duration"] = round(probe_duration(ref), 1)
            voice["updated"] = ref.stat().st_mtime
        except Exception:
            pass
        voice["url"] = f"/api/voice/audio?v={int(ref.stat().st_mtime)}"

    queue_data = {status: [_queue_item(m, status) for m in queue.list_shorts(status)]
                  for status in ("pending", "approved", "published")}

    hub = ROOT / ".cache" / "huggingface" / "hub"
    whisper = str(get(cfg, "captions.model", "small"))
    platforms = _platform_state(cfg)

    return {
        "app": {
            "version": __version__,
            "root": str(ROOT),
            "niche": get(cfg, "project.niche", ""),
            "voice_provider": get(cfg, "voice.provider", "space"),
            "edge_voice": get(cfg, "voice.edge_voice", "es-ES-AlvaroNeural"),
            "script_provider": get(cfg, "script.provider", "template"),
            "graphics_provider": get(cfg, "graphics.provider", "hyperframes"),
            "language": get(cfg, "project.language", "es"),
            "target_duration": int(get(cfg, "project.target_duration", 90) or 90),
            "min_duration": int(get(cfg, "project.min_duration", 60) or 60),
            "max_duration": int(get(cfg, "project.max_duration", 120) or 120),
            "providers": script_gen.available_providers(cfg),
            "research": bool(get(cfg, "script.research", True)),
        },
        "voice": voice,
        "resources": _resource_list(),
        "topics": _topic_list(cfg),
        "queue": {
            "pending": queue_data["pending"],
            "approved": queue_data["approved"],
            "published": queue_data["published"],
            "counts": {k: len(v) for k, v in queue_data.items()},
        },
        "system": {
            "disk_free_gb": round(disk_free_gb(ROOT), 1),
            "whisper": whisper,
            "whisper_cached": (hub / f"models--Systran--faster-whisper-{whisper}").exists(),
            "ffmpeg": True,
            "graphics": graphics.status(),
            "keys": {
                "pexels": bool(env("PEXELS_API_KEY")),
                "pixabay": bool(env("PIXABAY_API_KEY")),
                "gemini": bool(env("GEMINI_API_KEY")),
                "openai": bool(env("OPENAI_API_KEY")),
                "github": bool(env("GITHUB_TOKEN") or env("GITHUB_MODELS_TOKEN")),
                "unsplash": bool(env("UNSPLASH_ACCESS_KEY")),
                "openverse": bool(env("OPENVERSE_API_KEY")),
            },
        },
        "settings": settings_payload(),
        "publish": {
            "platforms": platforms,
            "auto": bool(get(cfg, "publish.auto_publish", False)),
        },
        "job": JOB.snapshot(),
        "estimate": round(_estimate_seconds(), 0),
    }


# ------------------------------------------------------------------ servidor

class Handler(BaseHTTPRequestHandler):
    server_version = f"AngelProduceIA/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- utilidades -------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - silencio
        pass

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(min(length, MAX_BODY))

    def _body_json(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _send_file(self, path: Path, download: bool = False) -> None:
        if not path.is_file():
            self._error("Ese archivo ya no existe.", 404)
            return
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        ctype = {
            "video/mp4": "video/mp4",
            "audio/x-wav": "audio/wav",
            "audio/wav": "audio/wav",
        }.get(ctype, ctype)
        range_header = self.headers.get("Range", "")
        start, end = 0, size - 1
        partial = False
        if range_header.startswith("bytes="):
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                first, second = match.group(1), match.group(2)
                if first:
                    start = int(first)
                    if second:
                        end = min(int(second), size - 1)
                elif second:
                    start = max(0, size - int(second))
                partial = True
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.send_header("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))
        self.end_headers()
        try:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _safe_path(self, rel: str) -> Path | None:
        if not rel:
            return None
        candidate = (ROOT / rel).resolve()
        try:
            candidate.relative_to(ROOT)
        except ValueError:
            return None
        return candidate

    # -- GET --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        try:
            if route == "/api/ping":
                self._json({"ok": True, "version": __version__, "pid": os.getpid()})
                return
            if route == "/api/state":
                self._json(build_state())
                return
            if route == "/api/autopilot":
                from . import autopilot as _ap
                self._json({"config": _ap.load(), "runs": _ap.runs(), "due": _ap.is_due()})
                return
            if route == "/api/oauth/providers":
                apps = oauth.load_apps()
                providers = []
                for pid, meta in oauth.PROVIDERS.items():
                    app = apps.get(pid, {})
                    providers.append({
                        "id": pid,
                        "label": meta["label"],
                        "configured": bool(app.get("client_id")),
                        "redirect_uri": app.get("redirect_uri", ""),
                        "scopes": meta["scopes"],
                    })
                self._json({"providers": providers})
                return
            if route == "/api/oauth/connections":
                provider = (query.get("provider") or [None])[0]
                self._json({"connections": oauth.list_connections(provider)})
                return
            if route == "/api/zernio/status":
                from . import publish as _pub
                verified = _pub.zernio.verify()
                self._json({
                    "configured": bool(_pub.zernio.ready(load_config())),
                    "verified": bool(verified.get("ok")),
                    "verify_error": verified.get("error"),
                    "accounts": _pub.zernio.list_accounts() if verified.get("ok") else [],
                })
                return
            if route == "/api/zernio/accounts":
                from . import publish as _pub
                self._json({"accounts": _pub.zernio.list_accounts()})
                return

            if route == "/api/gta/search":
                from . import gta_media as _gm
                q = (query.get("q") or ["GTA 6"])[0]
                sort = (query.get("sort") or ["relevance"])[0]
                data = _gm.search_all(q, sort)
                self._json(data)
                return

            if route == "/api/youtube-clips/status":
                from . import youtube_clips as _yc
                self._json({"ok": True, **_yc.get_state()})
                return

            if route == "/api/gta/test":
                from . import gta_media as _gm
                results = {
                    "newsapi": _gm.test_newsapi(),
                    "igdb": _gm.test_igdb(),
                    "youtube": _gm.test_youtube(),
                }
                self._json({"ok": True, "results": results})
                return

            if route == "/api/gta/test":
                from . import gta_media as _gm
                results = {
                    "newsapi": _gm.test_newsapi(),
                    "igdb": _gm.test_igdb(),
                    "youtube": _gm.test_youtube(),
                }
                self._json({"ok": True, "results": results})
                return
            if route == "/api/oauth/start":
                provider = (query.get("provider") or [""])[0]
                redirect_uri = (query.get("redirect_uri") or [None])[0]
                result = oauth.start_auth(provider, redirect_uri)
                self._json(result)
                return
            if route == "/api/oauth/callback":
                code = (query.get("code") or [""])[0]
                state = (query.get("state") or [""])[0]
                # determine provider from state
                entry = oauth.validate_state(state)
                provider = entry.get("provider") if entry else ""
                if not provider:
                    self._redirect("/#oauth=error")
                    return
                result = oauth.exchange_code(provider, code, state)
                if result.get("ok"):
                    self._redirect("/#oauth=ok&provider=" + provider)
                else:
                    self._redirect("/#oauth=error&msg=" + urllib.parse.quote(result.get("error", "")))
                return
            if route == "/api/settings":
                self._json(settings_payload())
                return
            if route == "/api/job":
                self._json(JOB.snapshot())
                return
            if route == "/api/voice/audio":
                ref = path_of(get(load_config(), "voice.ref_audio", "assets/voice/reference.wav"))
                self._send_file(ref)
                return
            if route == "/api/resources/file":
                target = _resource_path((query.get("name") or [""])[0])
                if target is None:
                    self._error("Ese recurso ya no existe.", 404)
                    return
                self._send_file(target)
                return
            if route == "/media":
                target = self._safe_path((query.get("rel") or [""])[0])
                if target is None:
                    self._error("Ruta no permitida.", 403)
                    return
                self._send_file(target)
                return
            if route == "/favicon.ico":
                icon = WEB / "icon.png"
                if icon.exists():
                    self._send_file(icon)
                else:
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                return
            if route.startswith("/frames/"):
                name = route[len("/frames/"):]
                allowed = {entry["file"] for entry in FRAME_TEMPLATES.values()}
                target = FRAME_DIR / name
                if name in allowed and target.is_file():
                    self._send_file(target)
                else:
                    self._error("Marco no encontrado.", 404)
                return
            name = STATIC.get(route)
            if name:
                target = WEB / name
                if not target.exists():
                    self._error(f"Falta {name}. Reinstala el proyecto.", 500)
                    return
                self._send_file(target)
                return
            self._error("No encontrado.", 404)
        except Exception as exc:  # pragma: no cover - red de seguridad
            _log_exception(exc)
            self._error(f"Error interno: {exc}", 500)

    # -- POST -------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        try:
            if route == "/api/run":
                data = self._body_json()
                count = max(1, min(10, int(data.get("count") or 1)))
                topic = (data.get("topic") or "").strip()
                sources = _run_sources(data)
                manual = str(data.get("script") or "").strip()
                if len(manual) > 30_000:
                    self._error("El guion manual es demasiado largo (máximo 30.000 caracteres).", 400)
                    return
                resources: list[Path] = []
                requested = data.get("resources")
                missing: list[str] = []
                if isinstance(requested, list):
                    for raw in requested[:MAX_RESOURCES]:
                        clean = str(raw or "")
                        target = _resource_path(clean)
                        if target is not None:
                            resources.append(target)
                        elif clean:
                            missing.append(clean)
                if missing and not resources:
                    self._error(
                        "Los recursos seleccionados ya no existen. "
                        "Recarga la página y vuelve a elegirlos.", 400)
                    return
                args = ["run", "-n", str(count)]
                if topic:
                    args += ["-t", topic]
                try:
                    seconds = float(data.get("seconds") or 0)
                except (TypeError, ValueError):
                    seconds = 0.0
                if seconds > 0:
                    args += ["-d", str(int(max(60, min(120, seconds))))]
                if sources:
                    args += ["--sources", *sources]
                if manual:
                    try:
                        args += ["--script-file", str(_manual_script_file(manual))]
                    except Exception as exc:
                        self._error(f"No pude guardar el guion manual: {exc}", 500)
                        return
                for path in resources:
                    args += ["--resource", str(path)]
                label = f"Generando {count} short{'s' if count > 1 else ''}"
                if manual:
                    label += " · guion manual"
                elif sources:
                    label += " · fuentes web"
                if resources:
                    label += " · tus recursos"
                started = JOB.start("run", args, label, _estimate_seconds() * count)
                if not started:
                    self._error("Ya hay una tarea en marcha. Espera o cancélala.", 409)
                    return
                self._json({"ok": True, "job": JOB.snapshot()})
                return

            if route == "/api/cancel":
                self._json({"ok": JOB.cancel()})
                return

            if route == "/api/settings/keys":
                data = self._body_json()
                saved = save_keys(data)
                self._json({"ok": True, "saved": saved, "settings": settings_payload()})
                return

            if route == "/api/settings/voice":
                data = self._body_json()
                provider = str(data.get("provider") or "").strip().lower()
                edge_voice = str(data.get("edge_voice") or "").strip()
                if provider not in {"auto", "edge", "local", "space", "elevenlabs"}:
                    self._error("Proveedor no válido.", 400)
                    return
                try:
                    _save_voice_settings(provider, edge_voice)
                except Exception as exc:
                    self._error(str(exc))
                    return
                self._json({"ok": True, "settings": settings_payload()})
                return

            if route == "/api/settings/script":
                data = self._body_json()
                provider = str(data.get("provider") or "").strip().lower()
                if provider not in {"auto", "openai", "gemini", "github", "template"}:
                    self._error("Proveedor de guion no válido.", 400)
                    return
                try:
                    _save_script_provider(provider)
                except Exception as exc:
                    self._error(str(exc))
                    return
                self._json({"ok": True, "settings": settings_payload()})
                return

            if route == "/api/settings/media":
                data = self._body_json()
                source = str(data.get("source") or "").strip().lower()
                if source not in {"auto", "local", "internet", "mixed", "hyperframes"}:
                    self._error("Fuente no válida.", 400)
                    return
                # "internet" se mapea a "auto" (intenta online).
                if source == "internet":
                    source = "auto"
                try:
                    _save_media_source(source)
                except Exception as exc:
                    self._error(str(exc))
                    return
                self._json({"ok": True, "settings": settings_payload()})
                return

            if route == "/api/settings/voice-tone":
                data = self._body_json()
                tone = str(data.get("tone") or "").strip().lower()
                if tone not in {"breaking", "narrative", "calm", "motivational"}:
                    self._error("Tono no válido.", 400)
                    return
                try:
                    _save_voice_tone(tone)
                except Exception as exc:
                    self._error(str(exc))
                    return
                self._json({"ok": True, "settings": settings_payload()})
                return

            if route == "/api/settings/music":
                data = self._body_json()
                values = {}
                for key in _MUSIC_LIMITS:
                    if key in data and data[key] is not None:
                        try:
                            values[key] = float(data[key])
                        except (TypeError, ValueError):
                            self._error("Ajuste de música no válido.", 400)
                            return
                if not values:
                    self._error("Falta el ajuste de música.", 400)
                    return
                try:
                    _save_music_settings(values)
                except Exception as exc:
                    self._error(str(exc))
                    return
                self._json({"ok": True, "settings": settings_payload()})
                return

            if route == "/api/settings/frame":
                data = self._body_json()
                name = str(data.get("frame_template") or "").strip().lower()
                if name and name not in FRAME_TEMPLATES:
                    self._error("Ese marco no existe.", 400)
                    return
                if name and not (FRAME_DIR / FRAME_TEMPLATES[name]["file"]).is_file():
                    self._error("Falta el PNG de ese marco.", 400)
                    return
                try:
                    _save_frame_template(name)
                except Exception as exc:
                    self._error(str(exc))
                    return
                self._json({"ok": True, "settings": settings_payload()})
                return

            if route == "/api/settings/voice-intensity":
                data = self._body_json()
                try:
                    percent = int(data.get("intensity"))
                except (TypeError, ValueError):
                    self._error("Intensidad no valida.", 400)
                    return
                try:
                    _save_voice_intensity(percent)
                except Exception as exc:
                    self._error(str(exc))
                    return
                self._json({"ok": True, "settings": settings_payload()})
                return


                from . import gta_media as _gm
                results = {
                    "newsapi": _gm.test_newsapi(),
                    "igdb": _gm.test_igdb(),
                    "youtube": _gm.test_youtube(),
                }
                self._json({"ok": True, "results": results})
                return

            if route == "/api/youtube-clips":
                data = self._body_json()
                url = str(data.get("url") or "").strip()
                resource = str(data.get("resource") or "").strip()
                count = max(1, min(10, int(data.get("count") or 1)))
                target_sec = max(60, min(300, int(data.get("duration") or 60)))
                subtitles = bool(data.get("subtitles", True))
                source_video = None
                if resource:
                    source_video = _resource_path(resource)
                    if source_video is None or _resource_kind(source_video) != "video":
                        self._error("Elige un vídeo subido válido.", 400)
                        return
                elif not url:
                    self._error("Pega un enlace o elige un vídeo subido.", 400)
                    return
                from . import youtube_clips as _yc
                _yc.generate_async(url, count, target_sec, subtitles, source_video)
                self._json({"ok": True, "source": "video" if source_video else "youtube"})
                return

            if route == "/api/youtube-clips/delete":
                data = self._body_json()
                rel = str(data.get("path") or "").strip()
                if not rel:
                    self._error("Falta la ruta.", 400)
                    return
                from pathlib import Path as _P
                from . import youtube_clips as _yc
                target = (_yc.CLIPS_DIR / _P(rel).name).resolve() if False else None
                # Resolve relative to ROOT and ensure under CLIPS_DIR
                candidate = (ROOT / rel).resolve() if not _P(rel).is_absolute() else _P(rel).resolve()
                try:
                    candidate.relative_to(_yc.CLIPS_DIR.resolve())
                except ValueError:
                    self._error("Ruta no permitida.", 403)
                    return
                try:
                    if candidate.exists():
                        candidate.unlink()
                    # also remove siblings (ass, etc.) not needed
                except Exception as exc:
                    self._error(str(exc))
                    return
                # remove from state
                _yc._STATE["results"] = [r for r in _yc._STATE.get("results", [])
                                          if r.get("path") != rel and r.get("path") != str(candidate)]
                self._json({"ok": True})
                return

            if route == "/api/youtube-clips/stop":
                from . import youtube_clips as _yc
                _yc._STATE["running"] = False
                self._json({"ok": True})
                return

            if route == "/api/autopilot":
                from . import autopilot as _ap
                data = self._body_json()
                cfg = _ap.save(data)
                self._json({"ok": True, "config": cfg})
                return

            if route == "/api/autopilot/run":
                from . import autopilot as _ap
                result = _ap.run_once(force=True)
                self._json(result)
                return

            if route == "/api/oauth/apps":
                data = self._body_json()
                provider = str(data.get("provider") or "").strip()
                try:
                    result = oauth.save_app(
                        provider,
                        str(data.get("client_id") or ""),
                        str(data.get("client_secret") or ""),
                        str(data.get("redirect_uri") or ""),
                    )
                    self._json({"ok": True, **result})
                except Exception as exc:
                    self._error(str(exc))
                return

            if route == "/api/oauth/disconnect":
                data = self._body_json()
                channel_id = str(data.get("channel_id") or "").strip()
                ok = oauth.disconnect(channel_id)
                self._json({"ok": ok})
                return

            if route == "/api/zernio/key":
                data = self._body_json()
                value = str(data.get("api_key") or "").strip()
                if not value:
                    self._error("Falta la API key.")
                    return
                import os
                from .config import ROOT
                env_path = ROOT / ".env"
                lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
                existing = None
                for i, line in enumerate(lines):
                    if line.strip().startswith("ZERNIO_API_KEY="):
                        existing = i
                        break
                if existing is None:
                    lines.append(f"ZERNIO_API_KEY={value}")
                else:
                    lines[existing] = f"ZERNIO_API_KEY={value}"
                env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.environ["ZERNIO_API_KEY"] = value
                self._json({"ok": True})
                return

            if route == "/api/zernio/test":
                from . import publish as _pub
                try:
                    result = _pub.zernio.verify()
                    if result.get("ok"):
                        accounts = _pub.zernio.list_accounts()
                        self._json({"ok": True, "accounts": accounts})
                    else:
                        self._json({"ok": False, "error": result.get("error", "Error"), "status": result.get("status", 0)})
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)[:200]})
                return

            if route == "/api/approve":
                data = self._body_json()
                target = (data.get("id") or "").strip()
                pending = queue.list_shorts("pending")
                if target in ("", "all"):
                    ids = [m["id"] for m in pending]
                else:
                    ids = [target]
                moved = []
                for short_id in ids:
                    status, _ = queue.find_short(short_id)
                    if status == "pending":
                        queue.approve_short(short_id)
                        moved.append(short_id)
                self._json({"ok": True, "approved": moved})
                return

            if route == "/api/reject":
                data = self._body_json()
                target = (data.get("id") or "").strip()
                pending = queue.list_shorts("pending")
                if target in ("", "all"):
                    ids = [m["id"] for m in pending]
                else:
                    ids = [target]
                removed = []
                for short_id in ids:
                    if (PENDING / short_id).exists():
                        queue.reject_short(short_id)
                        removed.append(short_id)
                self._json({"ok": True, "rejected": removed})
                return

            if route == "/api/delete":
                data = self._body_json()
                target = (data.get("id") or "").strip()
                if not target:
                    self._error("Falta el id.", 400)
                    return
                removed = queue.delete_short(target)
                self._json({"ok": removed})
                return

            if route == "/api/graphics/install":
                data = self._body_json()
                name = str(data.get("name") or "").strip()
                if not name:
                    self._error("Falta el nombre de la plantilla.", 400)
                    return
                try:
                    installed = graphics.install(name)
                except Exception as exc:
                    self._error(str(exc), 400)
                    return
                self._json({"ok": True, "name": installed, "graphics": graphics.status()})
                return

            if route == "/api/publish":
                data = self._body_json()
                approved = queue.list_shorts("approved")
                if not approved:
                    self._error("No hay shorts aprobados todavía.", 400)
                    return
                args = ["publish"]
                if data.get("dry_run"):
                    args.append("--dry-run")
                else:
                    if not any(p["state"] == "ready" for p in _platform_state(load_config())):
                        self._error("Ninguna plataforma está conectada. Configúrala en Ajustes.", 400)
                        return
                label = "Simulando publicación" if data.get("dry_run") else "Publicando"
                started = JOB.start("publish", args, label, 180.0)
                if not started:
                    self._error("Ya hay una tarea en marcha.", 409)
                    return
                self._json({"ok": True, "job": JOB.snapshot()})
                return

            if route == "/api/voice":
                self._upload_voice(query)
                return

            if route == "/api/resources":
                self._upload_resource(query)
                return

            if route == "/api/resources/delete":
                self._delete_resource()
                return

            if route == "/api/open":
                data = self._body_json()
                rel = (data.get("rel") or "").strip()
                target = self._safe_path(rel) if rel else ROOT
                if target is None or not target.exists():
                    self._error("Esa carpeta no existe.", 404)
                    return
                os.startfile(str(target))  # type: ignore[attr-defined]
                self._json({"ok": True})
                return

            if route == "/api/shutdown":
                self._json({"ok": True})
                JOB.cancel()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            self._error("No encontrado.", 404)
        except Exception as exc:  # pragma: no cover - red de seguridad
            _log_exception(exc)
            self._error(f"Error interno: {exc}", 500)

    # -- recursos propios -------------------------------------------------

    def _upload_resource(self, query: dict) -> None:
        """Guarda una imagen o vídeo propio sin cargarlo entero en memoria."""
        filename = str((query.get("name") or ["recurso"])[0])
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_EXT | VIDEO_EXT:
            self._error("Solo acepto imágenes JPG/PNG/WEBP o vídeos MP4/MOV/MKV/WEBM/M4V.", 400)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._error("El archivo llegó vacío. Prueba otra vez.", 400)
            return
        if length > MAX_RESOURCE_BYTES:
            self.close_connection = True
            self._error(
                f"El archivo pasa de {MAX_RESOURCE_BYTES // (1024 * 1024)} MB. "
                "Recórtalo o usa una versión más ligera.", 413)
            return
        RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
        if len(_resource_list()) >= MAX_RESOURCES:
            self.close_connection = True
            self._error(f"Máximo {MAX_RESOURCES} recursos guardados. Borra alguno antes de subir más.", 400)
            return

        target = _unique_resource_path(filename)
        part = target.with_name(target.name + ".part")
        received = 0
        try:
            with part.open("wb") as handle:
                while received < length:
                    chunk = self.rfile.read(min(1024 * 1024, length - received))
                    if not chunk:
                        raise OSError("La conexión se cortó antes de terminar la subida.")
                    handle.write(chunk)
                    received += len(chunk)
            if received < 64:
                raise ValueError("El archivo está vacío o es demasiado pequeño.")
            if suffix in VIDEO_EXT and probe_duration(part) <= 0:
                raise ValueError("No pude leer ese vídeo. Prueba con MP4 (H.264) o WEBM.")
            part.replace(target)
        except Exception as exc:
            try:
                part.unlink(missing_ok=True)
            except Exception:
                pass
            self._error(f"No pude guardar el recurso: {exc}", 400)
            return
        self._json({"ok": True, "resource": _resource_item(target)})

    def _delete_resource(self) -> None:
        data = self._body_json()
        target = _resource_path(str(data.get("name") or ""))
        if target is None:
            self._error("Ese recurso ya no existe.", 404)
            return
        try:
            target.unlink()
        except Exception as exc:
            self._error(f"No pude borrar el recurso: {exc}", 500)
            return
        self._json({"ok": True, "resources": _resource_list()})

    # -- subida de voz ----------------------------------------------------

    def _upload_voice(self, query: dict) -> None:
        filename = (query.get("name") or ["voz"])[0]
        suffix = Path(filename).suffix.lower() or ".wav"
        raw = self._body()
        if len(raw) < 2048:
            self._error("El archivo llegó vacío. Prueba otra vez.", 400)
            return
        work = WORK / f"upload-{int(time.time())}"
        work.mkdir(parents=True, exist_ok=True)
        source = work / f"voz{suffix}"
        source.write_bytes(raw)
        try:
            cfg = load_config()
            ref = path_of(get(cfg, "voice.ref_audio", "assets/voice/reference.wav"))
            old_text = path_of(get(cfg, "voice.ref_text", "assets/voice/reference.txt"))
            tts.prepare_reference(source, ref)
            if old_text.exists():
                old_text.unlink()
            duration = probe_duration(ref)
            self._json({"ok": True, "duration": round(duration, 1), "state": build_state()})
        except Exception as exc:
            _log_exception(exc)
            self._error(f"No pude preparar esa voz: {exc}", 400)
        finally:
            try:
                source.unlink(missing_ok=True)
                work.rmdir()
            except Exception:
                pass


def _log_exception(exc: Exception) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with (LOGS / "ui.log").open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {exc}\n")
            handle.write(traceback.format_exc())
    except Exception:
        pass


# ------------------------------------------------------------------ arranque

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        # El navegador corta conexiones constantemente: no ensuciamos el registro.
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        if isinstance(exc, Exception):
            _log_exception(exc)


def _serve(port: int) -> ThreadingHTTPServer:
    last: Exception | None = None
    for candidate in range(port, port + 25):
        try:
            return Server(("0.0.0.0", candidate), Handler)
        except OSError as exc:
            last = exc
    raise RuntimeError(f"No encontré un puerto libre cerca del {port}: {last}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Interfaz web local de ANGEL PRODUCE IA")
    parser.add_argument("--port", type=int, default=8765, help="Puerto (por defecto 8765)")
    parser.add_argument("--open", action="store_true", help="Abrir el navegador al arrancar")
    args = parser.parse_args(argv)

    setup_console()
    ensure_dirs()
    httpd = _serve(args.port)
    real_port = httpd.server_address[1]
    url = f"http://127.0.0.1:{real_port}"
    print(f"\n  ANGEL PRODUCE IA · interfaz lista en {url}")
    print("  Ciérrala desde el botón 'Salir' de la propia interfaz.\n", flush=True)
    if args.open:
        def _open_browser():
            try:
                from .graphics import browser_exe
                chrome = browser_exe()
                if chrome:
                    import subprocess
                    subprocess.Popen([str(chrome), url],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return
            except Exception:
                pass
            webbrowser.open(url)
        threading.Timer(0.6, _open_browser).start()
    try:
        httpd.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        pass
    finally:
        JOB.cancel()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
