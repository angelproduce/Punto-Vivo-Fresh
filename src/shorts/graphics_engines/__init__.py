"""Modular graphics engine system.

Supports: hyperframes, svg, lottie, remotion (optional). FFmpeg remains the compositor.
HyperFrames is optional. New engines can be registered via register().
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..config import get
from ..util import info, warn


def _has(text: str, terms: tuple[str, ...]) -> bool:
    """Coincidencia por palabra completa (evita «ia» dentro de «justicia»)."""
    for term in terms:
        if re.search(rf"(?<![a-záéíóúüñ0-9]){re.escape(term)}(?![a-záéíóúüñ0-9])", text):
            return True
    return False


@dataclass
class OverlayAsset:
    """An overlay asset to be composited by FFmpeg."""
    path: Path
    kind: str = "video"  # video | image | image_seq
    start: float = 0.0
    duration: float | None = None
    x: str = "0"
    y: str = "0"
    w: str | None = None
    h: str | None = None
    alpha: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    label: str = ""


@dataclass
class GraphicsPlan:
    overlays: list[OverlayAsset] = field(default_factory=list)
    backgrounds: list[Path] = field(default_factory=list)
    style: str = "clean"
    engine: str = "none"


class GraphicsEngine(Protocol):
    name: str

    def available(self, cfg: dict) -> bool: ...
    def prepare(self, cfg: dict, script: Any, duration: float, work: Path, style: str) -> GraphicsPlan: ...


_registry: dict[str, Any] = {}


def register(name: str, engine: Any) -> None:
    _registry[name] = engine


def get_engine(name: str) -> Any | None:
    return _registry.get(name)


def list_engines(cfg: dict) -> list[dict]:
    out = []
    for n, eng in _registry.items():
        try:
            ok = eng.available(cfg)
        except Exception:
            ok = False
        out.append({"name": n, "available": ok})
    return out


def _detect_style(cfg: dict, script: Any) -> str:
    # explicit style override
    explicit = str(get(cfg, "graphics.style", "") or cfg.get("_style", "") or "").strip().lower()
    aliases = {"news": "periodistico", "clean": "documental",
               "hype": "videojuegos", "cinematic": "cinematografico",
               "trailer": "cinematografico", "vibrant": "tecnologico",
               "velocity": "videojuegos"}
    if explicit and explicit != "auto":
        return aliases.get(explicit, explicit)
    title = ((getattr(script, "title", "") or "") + " " + (getattr(script, "topic_id", "") or "")).lower()
    text = ((getattr(script, "full_text", "") or "") + " " + title).lower()
    if _has(text, ("gta", "grand theft", "videojuego", "videojuegos", "gaming",
                   "gameplay", "ps5", "xbox")):
        return "videojuegos"
    if _has(text, ("estadística", "estadisticas", "datos", "porcentaje",
                   "gráfico", "grafica", "%", "millones", "cifra")):
        return "stats"
    if _has(text, ("tecnología", "tecnologia", "ia", "inteligencia artificial",
                   "software", "app", "chip", "robot", "algoritmo")):
        return "tecnologico"
    if _has(text, ("documental", "historia", "origen", "evolución", "evolucion")):
        return "documental"
    if _has(text, ("cine", "película", "pelicula", "trailer", "estreno")):
        return "cinematografico"
    if _has(text, ("noticia", "noticias", "breaking", "reportaje", "informó",
                   "informo", "según", "declaró", "gobierno", "elecciones")):
        return "periodistico"
    style_hint = str(cfg.get("_style", "") or "")
    mapping = {"news": "periodistico", "hype": "videojuegos", "cinematic": "cinematografico",
               "trailer": "cinematografico", "clean": "documental", "vibrant": "tecnologico",
               "velocity": "videojuegos"}
    return mapping.get(style_hint, "documental")


def detect_style(cfg: dict, script: Any) -> str:
    """Estilo público para que pipeline y gráficos usen el mismo criterio."""
    return _detect_style(cfg, script)


def prepare(cfg: dict, script: Any, duration: float, work: Path) -> GraphicsPlan:
    provider = str(get(cfg, "graphics.provider", "none") or "none").lower().strip()
    style = _detect_style(cfg, script)
    if provider in ("none", "degradados", "off", "false", "0", ""):
        return GraphicsPlan(style=style, engine="none")

    # lazy register engines
    _ensure_registered()

    candidates = []
    if provider == "auto":
        candidates = ["svg", "hyperframes", "lottie", "remotion"]
    else:
        candidates = [provider, "svg"] if provider != "svg" else ["svg"]

    for name in candidates:
        eng = get_engine(name)
        if not eng:
            continue
        try:
            if not eng.available(cfg):
                continue
            plan = eng.prepare(cfg, script, duration, work, style)
            if plan and (plan.overlays or plan.backgrounds):
                plan.style = style
                plan.engine = name
                info(f"Gráficos: motor={name} estilo={style}")
                return plan
        except Exception as e:
            warn(f"Motor gráfico '{name}' falló: {str(e)[:80]}")

    info(f"Gráficos: sin motor disponible (estilo={style}), usando FFmpeg base.")
    return GraphicsPlan(style=style, engine="none")


_registered = False

def _ensure_registered() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    try:
        from .hyperframes_engine import HyperFramesEngine
        register("hyperframes", HyperFramesEngine())
    except Exception:
        pass
    try:
        from .svg_engine import SvgEngine
        register("svg", SvgEngine())
    except Exception:
        pass
    try:
        from .lottie_engine import LottieEngine
        register("lottie", LottieEngine())
    except Exception:
        pass
    try:
        from .remotion_engine import RemotionEngine
        register("remotion", RemotionEngine())
    except Exception:
        pass
