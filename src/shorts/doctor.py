"""Diagnóstico: qué funciona y qué falta."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import env, get, load_config, path_of
from .util import (CACHE, FONT_DIR, ROOT, ensure_dirs, err, ffmpeg_exe, info,
                   ok, probe_duration, step, warn)


def _check_python() -> int:
    problems = 0
    if sys.version_info < (3, 10):
        err(f"Python {sys.version.split()[0]} es demasiado antiguo (hace falta 3.10+).")
        problems += 1
    else:
        ok(f"Python {sys.version.split()[0]}")
    if sys.prefix == sys.base_prefix:
        warn("No estás dentro del entorno del proyecto (.venv). Usa run.cmd.")
    else:
        ok("Entorno portable activo (.venv)")
    return problems


def _check_ffmpeg() -> int:
    try:
        exe = ffmpeg_exe()
    except Exception as exc:
        err(f"FFmpeg: {exc}")
        return 1
    version = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout
    ok(f"FFmpeg: {version.splitlines()[0][:70]}")
    filters = subprocess.run([exe, "-hide_banner", "-filters"],
                             capture_output=True, text=True).stdout
    problems = 0
    for needed in ("subtitles", "drawtext", "gradients", "loudnorm", "concat"):
        if f" {needed} " in filters:
            ok(f"Filtro «{needed}» disponible")
        else:
            err(f"Falta el filtro «{needed}» en FFmpeg")
            problems += 1
    return problems


def _check_voice(cfg: dict) -> int:
    ref = path_of(get(cfg, "voice.ref_audio", "assets/voice/reference.wav"))
    if not ref.exists():
        warn("Falta tu voz de referencia. Súbela en la pestaña «Mi voz».")
        return 0
    seconds = probe_duration(ref)
    if seconds < 3:
        err(f"La referencia dura {seconds:.1f} s: demasiado corta.")
        return 1
    ok(f"Voz de referencia: {seconds:.1f} s · {ref.name}")
    return 0


def _check_models(cfg: dict) -> int:
    hub = Path(os.environ.get("HF_HOME", str(CACHE / "huggingface"))) / "hub"
    voice_model = str(get(cfg, "voice.model_local", "Qwen/Qwen3-TTS-12Hz-0.6B-Base"))
    slug = "models--" + voice_model.replace("/", "--")
    whisper = str(get(cfg, "captions.model", "small"))
    problems = 0
    if (hub / slug).exists():
        ok(f"Modelo de voz descargado ({voice_model.split('/')[-1]})")
    else:
        warn("El modelo de voz se descargará la primera vez (~1,5 GB).")
    if (hub / f"models--Systran--faster-whisper-{whisper}").exists():
        ok(f"Whisper «{whisper}» descargado")
    else:
        warn(f"Whisper «{whisper}» se descargará la primera vez (~500 MB).")
    return problems


def _check_topics(cfg: dict) -> int:
    from .script_gen import topic_list

    try:
        topics = topic_list(cfg)
    except Exception as exc:
        err(f"topics.yaml: {exc}")
        return 1
    free = len([t for t in topics if not t["used"]])
    ok(f"{len(topics)} temas · rotación automática (sin límite, {free} por estrenar)")
    if not topics:
        return 1
    return 0


def _check_keys(cfg: dict) -> int:
    found = []
    for name in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "GEMINI_API_KEY"):
        if env(name):
            found.append(name.split("_")[0].title())
    ok("Claves opcionales: " + (", ".join(found) if found else "ninguna (todo funciona igual)"))
    return 0


def _check_disk() -> int:
    from .util import disk_free_gb

    free = disk_free_gb(ROOT)
    if free < 4:
        err(f"Quedan {free:.1f} GB libres. Libera espacio antes de generar.")
        return 1
    ok(f"Disco libre: {free:.1f} GB")
    return 0


def _check_fonts() -> int:
    fonts = [p for p in FONT_DIR.glob("*.ttf")] if FONT_DIR.exists() else []
    if not fonts:
        windir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        if (windir / "arialbd.ttf").exists() or (windir / "ariblk.ttf").exists():
            warn("No hay fuentes en assets/fonts; usaré las de Windows.")
            return 0
        err("No hay ninguna fuente disponible para los textos.")
        return 1
    ok(f"Fuentes para los textos: {len(fonts)}")
    return 0


def _check_graphics() -> int:
    from . import graphics

    if not graphics.available():
        warn("HyperFrames no está disponible; usaré fondos de degradado propios.")
        return 0
    browser = graphics.browser_exe()
    items = graphics.templates()
    ok(f"HyperFrames listo: {len(items)} plantillas de gráficas")
    if browser:
        ok(f"Navegador para renderizar: {browser.name}")
    else:
        warn("No encuentro Chrome/Edge; el render de gráficas puede fallar.")
    return 0


def main() -> int:
    ensure_dirs()
    cfg = load_config()
    print(f"\n  ANGEL PRODUCE IA {__version__} · diagnóstico\n")
    problems = 0

    step("Sistema")
    problems += _check_python()
    problems += _check_ffmpeg()

    step("Proyecto")
    problems += _check_voice(cfg)
    problems += _check_topics(cfg)
    problems += _check_fonts()
    problems += _check_graphics()

    step("Modelos")
    problems += _check_models(cfg)

    step("Recursos")
    problems += _check_disk()
    _check_keys(cfg)

    print()
    if problems:
        err(f"{problems} problema(s). Revisa los mensajes de arriba.")
    else:
        ok("Todo listo. Abre la interfaz y genera tu primer short.")
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
