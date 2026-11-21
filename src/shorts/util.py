"""Utilidades compartidas: rutas, FFmpeg, consola y helpers."""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterator, Sequence

# ------------------------------------------------------------------ rutas
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "assets"
VOICE_DIR = ASSETS / "voice"
MUSIC_DIR = ASSETS / "music"
SFX_DIR = ASSETS / "sfx"
BG_DIR = ASSETS / "backgrounds"
FONT_DIR = ASSETS / "fonts"
OUTPUT = ROOT / "output"
QUEUE = ROOT / "queue"
PENDING = QUEUE / "pending"
APPROVED = QUEUE / "approved"
PUBLISHED = QUEUE / "published"
STATE = ROOT / "state"
CACHE = ROOT / ".cache"
LOGS = ROOT / "logs"
WORK = CACHE / "work"

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}

# Todo lo pesado vive dentro del proyecto.
os.environ.setdefault("HF_HOME", str(CACHE / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "cyan": "\033[96m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "magenta": "\033[95m",
}

_USE_COLOR = False


def setup_console() -> None:
    """Activa colores y UTF-8 en la consola de Windows."""
    global _USE_COLOR
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
    _USE_COLOR = bool(getattr(sys.stdout, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


def _c(name: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"{_ANSI[name]}{text}{_ANSI['reset']}"


def step(text: str) -> None:
    print(f"\n{_c('cyan', '▸')} {_c('bold', text)}", flush=True)


def ok(text: str) -> None:
    print(f"  {_c('green', '✓')} {text}", flush=True)


def info(text: str) -> None:
    print(f"  {_c('dim', '·')} {text}", flush=True)


def warn(text: str) -> None:
    print(f"  {_c('yellow', '!')} {text}", flush=True)


def err(text: str) -> None:
    print(f"  {_c('red', '✗')} {text}", flush=True)


# ------------------------------------------------------------------ dirs
def hf_no_symlinks() -> None:
    """En Windows sin permisos de administrador, huggingface_hub falla al crear
    symlinks y a veces aborta la descarga. Forzamos su modo de copia."""
    if os.name != "nt":
        return
    try:
        from huggingface_hub import constants, file_download

        cache = str(Path(constants.HF_HUB_CACHE).expanduser().resolve())
        file_download._are_symlinks_supported_in_dir[cache] = False
    except Exception:
        pass


def ensure_dirs() -> None:
    for folder in (ASSETS, VOICE_DIR, MUSIC_DIR, BG_DIR, OUTPUT, PENDING,
                   APPROVED, PUBLISHED, STATE, CACHE, WORK, LOGS):
        folder.mkdir(parents=True, exist_ok=True)
    hf_no_symlinks()


# ------------------------------------------------------------------ ffmpeg
_FFMPEG: str | None = None


def ffmpeg_exe() -> str:
    """FFmpeg portable que instala imageio-ffmpeg (nada en el sistema)."""
    global _FFMPEG
    if _FFMPEG and Path(_FFMPEG).exists():
        return _FFMPEG
    try:
        import imageio_ffmpeg

        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise RuntimeError("No encuentro FFmpeg. Vuelve a ejecutar setup.cmd.")
        _FFMPEG = found
    return _FFMPEG


def run(cmd: Sequence[str], cwd: Path | None = None, timeout: float | None = None) -> str:
    """Ejecuta un proceso y devuelve su salida. Lanza RuntimeError si falla."""
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-12:])
        raise RuntimeError(tail or f"El comando falló ({proc.returncode}).")
    return out


def ffmpeg(args: Sequence[str], cwd: Path | None = None, timeout: float | None = None) -> str:
    return run([ffmpeg_exe(), "-hide_banner", "-y", *args], cwd=cwd, timeout=timeout)


def probe_duration(path: Path) -> float:
    """Duración en segundos (audio o vídeo)."""
    path = Path(path)
    if not path.exists():
        return 0.0
    if path.suffix.lower() in {".wav", ".flac", ".ogg"}:
        try:
            import soundfile as sf

            return float(sf.info(str(path)).duration)
        except Exception:
            pass
    try:
        proc = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        pass
    return 0.0


# ------------------------------------------------------------------ texto
def slugify(text: str, limit: int = 50) -> str:
    raw = unicodedata.normalize("NFKD", str(text))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return (raw[:limit].rstrip("-")) or "short"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def fmt_dur(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0))
    minutes = int(seconds // 60)
    return f"{minutes}:{int(seconds % 60):02d}"


def shorten(text: str, limit: int = 60) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def disk_free_gb(path: Path | None = None) -> float:
    try:
        usage = shutil.disk_usage(str(path or ROOT))
        return usage.free / (1024 ** 3)
    except Exception:
        return 0.0


# ------------------------------------------------------------------ json
def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ varios
@contextlib.contextmanager
def quiet_imports() -> Iterator[None]:
    """Silencia avisos de librerías al importar (p. ej. el de SoX)."""
    try:
        old_out, old_err = os.dup(1), os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
    except Exception:
        yield
        return
    try:
        yield
    finally:
        try:
            os.dup2(old_out, 1)
            os.dup2(old_err, 2)
            os.close(devnull)
            os.close(old_out)
            os.close(old_err)
        except Exception:
            pass


def font_file(bold: bool = True) -> Path | None:
    """Busca una fuente potente dentro del proyecto o en Windows."""
    names = ["ariblk.ttf", "seguibl.ttf", "arialbd.ttf", "segoeuib.ttf"] if bold else \
            ["segoeui.ttf", "arial.ttf"]
    folders = [FONT_DIR]
    windir = os.environ.get("WINDIR", r"C:\Windows")
    folders.append(Path(windir) / "Fonts")
    for folder in folders:
        for name in names:
            candidate = folder / name
            if candidate.exists():
                return candidate
    try:
        import PIL

        for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
            candidate = Path(PIL.__file__).parent / "fonts" / name
            if candidate.exists():
                return candidate
    except Exception:
        pass
    return None
