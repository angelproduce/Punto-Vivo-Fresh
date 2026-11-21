"""SVG/CSS graphics engine - generates overlay assets via headless Chrome or FFmpeg fallback."""
from __future__ import annotations

import html
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import get
from ..util import ROOT, ffmpeg, font_file, info, warn
TOOLS = ROOT / "tools"
from . import GraphicsPlan, OverlayAsset
from .styles import get_style


def _browser_exe() -> Path | None:
    import os
    configured = os.environ.get("HYPERFRAMES_BROWSER_PATH", "").strip()
    if configured and Path(configured).exists():
        return Path(configured)
    candidates = [
        TOOLS / "browser" / "chrome" / "chrome.exe",
        TOOLS / "browser" / "chrome-headless-shell" / "chrome-headless-shell.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for c in candidates:
        if c and c.exists():
            return c
    return None


def _render_html_to_png(browser: Path, html_path: Path, out_png: Path, width: int, height: int) -> bool:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    # chrome --headless=new --screenshot --window-size
    cmds = [
        [str(browser), "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--screenshot={out_png}", f"--window-size={width},{height}",
         "--default-background-color=00000000", html_path.as_uri()],
        [str(browser), "--headless", "--disable-gpu", "--hide-scrollbars",
         f"--screenshot={out_png}", f"--window-size={width},{height}", html_path.as_uri()],
    ]
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            if out_png.exists() and out_png.stat().st_size > 500:
                return True
        except Exception:
            continue
    return False


def _make_title_card_html(title: str, subtitle: str, style: dict, w: int, h: int,
                          kicker: str = "ÚLTIMA HORA", brand: str = "") -> str:
    """Tarjeta de título documental: scrim con degradado, kicker, titular
    grande y una línea de acento. No es un rectángulo negro con texto."""
    pal = style["palette"]
    font = style["font"]
    t = html.escape(title[:90])
    s = html.escape(subtitle[:140])
    k = html.escape(kicker[:28])
    b = html.escape(brand[:40])
    size = 96 if len(title) <= 26 else (82 if len(title) <= 44 else 68)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}
body{{margin:0;width:{w}px;height:{h}px;background:transparent;font-family:{font};overflow:hidden}}
.scrim{{position:absolute;left:0;right:0;top:140px;height:1320px;
  background:linear-gradient(180deg,rgba(0,0,0,0) 0%,rgba(0,0,0,.55) 20%,rgba(0,0,0,.74) 52%,rgba(0,0,0,.55) 78%,rgba(0,0,0,0) 100%)}}
.brand{{position:absolute;left:64px;top:164px;color:rgba(255,255,255,.82);font-size:27px;
  letter-spacing:3px;font-weight:700;text-transform:uppercase;text-shadow:0 2px 12px #000}}
.card{{position:absolute;left:64px;right:64px;top:350px}}
.kicker{{display:inline-block;background:{pal['accent']};color:#fff;font-size:27px;font-weight:800;
  letter-spacing:5px;padding:11px 20px;border-radius:3px;text-transform:uppercase;
  box-shadow:0 6px 24px rgba(0,0,0,.45)}}
.headline{{margin:24px 0 0;color:#fff;font-size:{size}px;line-height:1.03;font-weight:900;
  letter-spacing:-1px;text-shadow:0 5px 30px rgba(0,0,0,.95),0 1px 4px rgba(0,0,0,.95)}}
.accent{{width:220px;height:9px;margin-top:28px;border-radius:2px;
  background:linear-gradient(90deg,{pal['accent']},{pal['primary']})}}
.sub{{margin-top:24px;max-width:930px;color:rgba(255,255,255,.93);font-size:34px;
  line-height:1.30;font-weight:600;text-shadow:0 3px 18px rgba(0,0,0,.95)}}
</style></head><body><div class="scrim"></div>
<div class="brand">{b}</div>
<div class="card"><span class="kicker">{k}</span><h1 class="headline">{t}</h1>
<div class="accent"></div><p class="sub">{s}</p></div></body></html>"""


def _make_lower_third_html(text: str, style: dict, w: int, h: int) -> str:
    pal = style["palette"]
    font = style["font"]
    t = html.escape(text[:100])
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;width:{w}px;height:{h}px;background:transparent;font-family:{font}}}
.bar{{position:absolute;left:50px;right:50px;bottom:180px;height:86px;display:flex;align-items:center;padding:0 26px;
background:linear-gradient(90deg,{pal['accent']}EE,{pal['bg']}DD);border-radius:10px}}
.bar span{{color:{pal['text']};font-size:36px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
</style></head><body><div class="bar"><span>{t}</span></div></body></html>"""


def _make_ticker_html(items: list[str], style: dict, w: int, h: int) -> str:
    pal = style["palette"]
    font = style["font"]
    txt = "  •  ".join(html.escape(i[:60]) for i in items[:6]) or "NOTICIAS"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;width:{w}px;height:{h}px;background:transparent;font-family:{font}}}
.t{{position:absolute;left:0;right:0;top:120px;height:64px;background:{pal['accent']}DD;display:flex;align-items:center;padding:0 24px}}
.t span{{color:#fff;font-size:30px;font-weight:800}}
</style></head><body><div class="t"><span>{txt}</span></div></body></html>"""


def _make_stats_html(stats: list[dict], style: dict, w: int, h: int) -> str:
    pal = style["palette"]
    font = style["font"]
    rows = []
    for st in stats[:3]:
        label = html.escape(str(st.get("label", ""))[:40])
        value = html.escape(str(st.get("value", ""))[:20])
        rows.append(f'<div class="row"><span class="v">{value}</span><span class="l">{label}</span></div>')
    body = "".join(rows) or '<div class="row"><span class="v">—</span><span class="l">Datos</span></div>'
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;width:{w}px;height:{h}px;background:transparent;font-family:{font}}}
.panel{{position:absolute;left:70px;right:70px;top:420px;padding:22px;background:{pal['bg']}CC;border:2px solid {pal['accent']}88;border-radius:16px}}
.row{{display:flex;align-items:baseline;gap:16px;margin:10px 0}}
.v{{color:{pal['accent']};font-size:56px;font-weight:900;min-width:160px}}
.l{{color:{pal['text']};font-size:30px}}
</style></head><body><div class="panel">{body}</div></body></html>"""


def _extract_stats(text: str) -> list[dict]:
    import re
    stats = []
    # find percentages and numbers with context
    for m in re.finditer(r'(\d+(?:[.,]\d+)?)\s*(%|por ciento|millones|mil|mmdp|dólares|dolares|personas|años)', text, re.I):
        val = m.group(1) + ("%" if "%" in m.group(2) or "por ciento" in m.group(2).lower() else " " + m.group(2))
        # grab a bit of context
        start = max(0, m.start() - 30)
        ctx = text[start:m.start()].strip().split()[-4:]
        label = " ".join(ctx) or "Dato"
        stats.append({"value": val, "label": label})
        if len(stats) >= 3:
            break
    return stats


def _rgb(value: str, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int, int]:
    raw = str(value or "").lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16), 255)
    except Exception:
        return (*default, 255)


def _wrap_px(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in str(text or "").split():
        trial = f"{current} {word}".strip()
        try:
            fits = draw.textlength(trial, font=font) <= max_width
        except Exception:
            fits = len(trial) <= 32
        if fits or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_title_png(title: str, subtitle: str, style_name: str,
                   w: int, h: int, out: Path) -> Path:
    """Respaldo sin navegador: tarjeta de título moderna dibujada con Pillow."""
    from PIL import Image, ImageDraw, ImageFont

    style = get_style(style_name)
    pal = style["palette"]
    accent = _rgb(pal["accent"], (204, 0, 0))
    primary = _rgb(pal["primary"], (255, 255, 255))

    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Scrim degradado (transparente arriba/abajo, oscuro en el centro).
    top, bottom = 140, 1480
    peak = 198
    for y in range(top, bottom):
        t = (y - top) / max(1, bottom - top)
        if t < 0.18:
            alpha = int(peak * (t / 0.18))
        elif t < 0.55:
            alpha = peak
        elif t < 0.82:
            alpha = int(peak * (1 - (t - 0.55) / 0.27 * 0.30))
        else:
            alpha = int(peak * 0.70 * (1 - (t - 0.82) / 0.18))
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, max(0, min(255, alpha))))

    bold = font_file(bold=True)

    def load(size: int):
        try:
            return ImageFont.truetype(str(bold), size)
        except Exception:
            return ImageFont.load_default()

    kicker = "ÚLTIMA HORA" if style_name in ("periodistico", "stats") else "DOCUMENTAL"
    kfont = load(30)
    kicker_w = draw.textlength(kicker, font=kfont)
    draw.rounded_rectangle([64, 350, 64 + int(kicker_w) + 40, 414], radius=4, fill=accent)
    draw.text((84, 366), kicker, font=kfont, fill=(255, 255, 255, 255))

    title_size = 92 if len(title) <= 26 else (78 if len(title) <= 44 else 64)
    tfont = load(title_size)
    lines = _wrap_px(draw, title, tfont, w - 128)
    y = 446
    for line in lines[:4]:
        draw.text((66, y + 5), line, font=tfont, fill=(0, 0, 0, 235))
        draw.text((64, y), line, font=tfont, fill=(255, 255, 255, 255))
        y += int(title_size * 1.06)

    draw.rectangle([64, y + 26, 284, y + 35], fill=accent)
    draw.rectangle([284, y + 26, 350, y + 35], fill=primary)
    y += 62

    if subtitle:
        sfont = load(34)
        for line in _wrap_px(draw, subtitle, sfont, w - 140)[:3]:
            draw.text((66, y + 4), line, font=sfont, fill=(0, 0, 0, 220))
            draw.text((64, y), line, font=sfont, fill=(255, 255, 255, 238))
            y += 46

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out


class SvgEngine:
    name = "svg"

    def available(self, cfg: dict) -> bool:
        # Always available - can fall back to FFmpeg-only overlays
        return True

    def prepare(self, cfg: dict, script: Any, duration: float, work: Path, style: str) -> GraphicsPlan:
        plan = GraphicsPlan(style=style, engine="svg")
        sdef = get_style(style)
        w = int(get(cfg, "video.width", 1080))
        h = int(get(cfg, "video.height", 1920))
        browser = _browser_exe()
        gfx_dir = work / "gfx"
        gfx_dir.mkdir(parents=True, exist_ok=True)
        title = getattr(script, "title", "") or ""
        topic = getattr(script, "topic_id", "") or ""
        full = getattr(script, "full_text", "") or ""
        hook_text = getattr(script, "hook", "") or title

        def render_html(name: str, html_str: str, start: float, dur: float, label: str):
            html_p = gfx_dir / f"{name}.html"
            png_p = gfx_dir / f"{name}.png"
            html_p.write_text(html_str, encoding="utf-8")
            ok = False
            if browser:
                ok = _render_html_to_png(browser, html_p, png_p, w, h)
            if ok and png_p.exists():
                plan.overlays.append(OverlayAsset(
                    path=png_p, kind="image", start=start, duration=dur,
                    x="0", y="0", alpha=1.0, fade_in=0.35, fade_out=0.35, label=label
                ))
            return ok

        # La intro dura 4 s y en ella se narra el título: la tarjeta aparece
        # sobre el fragmento real y el resto de gráficos entra con la narración.
        hook_sec = float(cfg.get("_hook_seconds")
                         or get(cfg, "video.hook_seconds", 4.0) or 4.0)
        hook_sec = max(0.0, min(hook_sec, max(0.0, duration - 1.0)))
        after_intro = hook_sec + 0.25
        brand = str(get(cfg, "project.brand", "") or "")
        kicker = "ÚLTIMA HORA" if style in ("periodistico", "stats") else "DOCUMENTAL"

        # Tarjeta de título sobre el fragmento real (no un rectángulo negro).
        if sdef.get("title_card"):
            sub = hook_text[:100] if hook_text and hook_text != title else ""
            dur = max(1.6, min(hook_sec - 0.35, 5.5)) if hook_sec > 1.0 \
                else min(3.5, max(1.6, duration * 0.12))
            start = 0.15 if hook_sec > 1.0 else min(4.5, duration * 0.1)
            render_html("title_card",
                        _make_title_card_html(title, sub, sdef, w, h,
                                              kicker=kicker, brand=brand),
                        start=start, dur=dur, label="title_card")

        if sdef.get("lower_third") and full:
            # Frase del segundo tramo, ya dentro de la narración.
            import re
            sentences = [x.strip() for x in re.split(r'(?<=[.!?])\s+', full) if len(x.strip()) > 10]
            lt_text = sentences[min(1, len(sentences) - 1)] if sentences else topic
            start = max(after_intro, min(duration * 0.35, after_intro + 3.0))
            render_html("lower_third", _make_lower_third_html(lt_text, sdef, w, h),
                        start=start, dur=min(4.0, max(2.0, duration * 0.15)),
                        label="lower_third")

        if sdef.get("ticker") and style == "periodistico":
            import re
            sentences = [x.strip()[:55] for x in re.split(r'(?<=[.!?])\s+', full) if len(x.strip()) > 8]
            render_html("ticker", _make_ticker_html(sentences[:5], sdef, w, h),
                        start=after_intro, dur=max(2.0, duration - after_intro - 0.6),
                        label="ticker")

        if sdef.get("stats"):
            stats = _extract_stats(full)
            if stats:
                start = max(after_intro + 1.0, min(duration * 0.45, after_intro + 6.0))
                render_html("stats", _make_stats_html(stats, sdef, w, h),
                            start=start, dur=min(4.0, max(2.0, duration * 0.15)),
                            label="stats")

        if not plan.overlays and not browser:
            info("SVG: sin navegador para renderizar; se usarán solo overlays FFmpeg base.")
        elif plan.overlays:
            info(f"SVG: {len(plan.overlays)} overlay(s) generados (estilo {sdef['label']}).")
        return plan
