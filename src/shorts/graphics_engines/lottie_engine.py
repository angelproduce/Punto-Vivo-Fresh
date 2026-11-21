"""Lottie engine (optional). Requires lottie rendering tools or headless browser with lottie-web."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..util import ROOT, info, warn
from . import GraphicsPlan

LOTTIE_DIR = ROOT / "assets" / "graphics" / "lottie"


class LottieEngine:
    name = "lottie"

    def available(self, cfg: dict) -> bool:
        # Available if we have lottie files and a renderer (browser or lottie tools)
        if not LOTTIE_DIR.exists():
            return False
        files = list(LOTTIE_DIR.glob("*.json")) + list(LOTTIE_DIR.glob("*.lottie"))
        if not files:
            return False
        # need browser for rendering
        try:
            from .svg_engine import _browser_exe
            return _browser_exe() is not None
        except Exception:
            return False

    def prepare(self, cfg: dict, script: Any, duration: float, work: Path, style: str) -> GraphicsPlan:
        plan = GraphicsPlan(style=style, engine="lottie")
        if not LOTTIE_DIR.exists():
            return plan
        files = list(LOTTIE_DIR.glob("*.json")) + list(LOTTIE_DIR.glob("*.lottie"))
        if not files:
            return plan
        try:
            from .svg_engine import _browser_exe, _render_html_to_png
            browser = _browser_exe()
            if not browser:
                return plan
            # Render first lottie as a static frame overlay (simple integration)
            # Full animation rendering would require frame sequence capture
            src = files[0]
            w = int(cfg.get("video", {}).get("width", 1080))
            h = int(cfg.get("video", {}).get("height", 1920))
            data = src.read_text(encoding="utf-8", errors="ignore")
            html_p = work / "gfx" / "lottie.html"
            png_p = work / "gfx" / "lottie.png"
            html_p.parent.mkdir(parents=True, exist_ok=True)
            html_p.write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js"></script>
<style>body{{margin:0;width:{w}px;height:{h}px;background:transparent}}#c{{width:{w}px;height:{h}px}}</style>
</head><body><div id="c"></div><script>
try{{var anim=lottie.loadAnimation({{container:document.getElementById('c'),renderer:'svg',loop:false,autoplay:false,animationData:{data}}}); anim.goToAndStop(30,true);}}catch(e){{}}
</script></body></html>""", encoding="utf-8")
            if _render_html_to_png(browser, html_p, png_p, w, h):
                from . import OverlayAsset
                plan.overlays.append(OverlayAsset(path=png_p, kind="image", start=2.0, duration=3.0,
                                                  fade_in=0.3, fade_out=0.3, label="lottie"))
                info("Lottie: overlay generado (frame estático).")
        except Exception as e:
            warn(f"Lottie: {str(e)[:60]}")
        return plan
