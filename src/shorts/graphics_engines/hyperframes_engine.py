"""HyperFrames engine wrapper (optional)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import GraphicsPlan


class HyperFramesEngine:
    name = "hyperframes"

    def available(self, cfg: dict) -> bool:
        try:
            from .. import graphics
            return graphics.available()
        except Exception:
            return False

    def prepare(self, cfg: dict, script: Any, duration: float, work: Path, style: str) -> GraphicsPlan:
        from .. import graphics
        plan = GraphicsPlan(style=style, engine="hyperframes")
        try:
            # HyperFrames produces background clips; integrate as backgrounds
            # Split into segments like media does
            count = max(1, min(5, int(duration // 12) or 1))
            seg = duration / count
            lengths = [seg] * count
            lengths[-1] = max(2.0, duration - sum(lengths[:-1]))
            bgs = graphics.prepare_backgrounds(cfg, lengths, work)
            if bgs:
                plan.backgrounds = bgs
        except Exception:
            pass
        return plan
