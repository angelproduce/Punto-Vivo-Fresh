"""Marcos decorativos (plantillas) para los Shorts.

Cada marco es un PNG vertical con el centro transparente: se superpone encima
del vídeo ya montado, así que no tapa la imagen central ni los subtítulos.
El marco activo se elige a mano en Ajustes → 🖼️ Marcos y se guarda en
`video.frame_template` (vacío = sin marco).
"""
from __future__ import annotations

from pathlib import Path

from .util import ROOT

FRAME_DIR = ROOT / "assets" / "frames"

# Clave que se guarda en config.yaml -> nombre visible y archivo del PNG.
FRAMES: dict[str, dict[str, str]] = {
    "gta6": {"file": "gta6.png", "label": "GTA 6"},
    "alofoke3": {"file": "alofoke3.png", "label": "CASA ALOFOKE 3"},
    "angelproduce": {"file": "angel-produce.png", "label": "ANGEL PRODUCE"},
}


def normalize(name: str | None) -> str:
    """Devuelve la clave del marco o "" si no es válida."""
    key = str(name or "").strip().lower()
    return key if key in FRAMES else ""


def frame_path(name: str | None) -> Path | None:
    key = normalize(name)
    if not key:
        return None
    path = FRAME_DIR / FRAMES[key]["file"]
    return path if path.is_file() else None


def frame_label(name: str | None) -> str:
    key = normalize(name)
    return FRAMES[key]["label"] if key else ""


def frame_choices() -> list[dict[str, object]]:
    """Lista para la UI: clave, etiqueta, archivo de vista previa y si existe."""
    out = []
    for key in FRAMES:
        path = FRAME_DIR / FRAMES[key]["file"]
        exists = path.is_file()
        out.append({
            "key": key,
            "label": FRAMES[key]["label"],
            "file": FRAMES[key]["file"],
            "available": exists,
            # Versión para que el navegador no muestre una vista previa vieja
            # cuando se reemplaza el PNG por otro con el mismo nombre.
            "version": int(path.stat().st_mtime) if exists else 0,
        })
    return out


def frame_overlay(name: str | None, duration: float):
    """OverlayAsset del marco, o None si no hay marco activo o falta el PNG."""
    path = frame_path(name)
    if path is None:
        return None
    from .graphics_engines import OverlayAsset

    return OverlayAsset(
        path=path,
        kind="image",
        start=0.0,
        duration=max(1.0, float(duration)),
        fade_in=0.0,
        fade_out=0.0,
        label="frame",
    )
