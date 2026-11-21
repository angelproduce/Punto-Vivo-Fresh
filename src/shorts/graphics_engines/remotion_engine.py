"""Remotion engine (optional). Integrates with a Remotion project if available."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..util import ROOT, info, warn
TOOLS = ROOT / "tools"
from . import GraphicsPlan

REMOTION_DIR = ROOT / "assets" / "graphics" / "remotion"


class RemotionEngine:
    name = "remotion"

    def _node(self) -> Path | None:
        # reuse node from tools (same as HyperFrames)
        for candidate in sorted(TOOLS.glob("node/*/node.exe")):
            if candidate.exists():
                return candidate
        # also check PATH
        n = shutil.which("node")
        return Path(n) if n else None

    def _npx(self) -> Path | None:
        for candidate in sorted(TOOLS.glob("node/*/npx.cmd")):
            if candidate.exists():
                return candidate
        n = shutil.which("npx")
        return Path(n) if n else None

    def available(self, cfg: dict) -> bool:
        if not REMOTION_DIR.exists():
            return False
        # check for package.json / remotion config
        has_project = (REMOTION_DIR / "package.json").exists() or (REMOTION_DIR / "remotion.config.ts").exists()
        if not has_project:
            return False
        return self._node() is not None

    def prepare(self, cfg: dict, script: Any, duration: float, work: Path, style: str) -> GraphicsPlan:
        plan = GraphicsPlan(style=style, engine="remotion")
        if not self.available(cfg):
            return plan
        node = self._node()
        npx = self._npx()
        if not node or not npx:
            return plan
        try:
            out = work / "remotion_overlay.mp4"
            props = {
                "title": getattr(script, "title", "") or "",
                "text": (getattr(script, "full_text", "") or "")[:500],
                "style": style,
                "durationInFrames": int(duration * 30),
                "fps": 30, "width": int(cfg.get("video", {}).get("width", 1080)),
                "height": int(cfg.get("video", {}).get("height", 1920)),
            }
            props_file = work / "remotion_props.json"
            props_file.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")
            # Generic remotion render command - composition name "Graphics" expected
            cmd = [str(npx), "remotion", "render", "Graphics", str(out),
                   f"--props={props_file}", "--log=error"]
            r = subprocess.run(cmd, cwd=str(REMOTION_DIR), capture_output=True, timeout=600)
            if out.exists() and out.stat().st_size > 5000:
                from . import OverlayAsset
                plan.overlays.append(OverlayAsset(path=out, kind="video", start=0.0, duration=duration, label="remotion"))
                info("Remotion: overlay renderizado.")
            else:
                warn(f"Remotion: render no produjo salida ({r.stderr.decode('utf-8','ignore')[:80]})")
        except Exception as e:
            warn(f"Remotion: {str(e)[:80]}")
        return plan
