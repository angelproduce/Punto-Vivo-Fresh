"""Gráficas animadas hechas con HyperFrames (HTML/CSS/WebGPU -> MP4).

HyperFrames es el motor de gráficos: componemos en HTML, lo renderiza en
vertical (1080x1920) y el resultado se usa como fondo del short. Todo queda
en la carpeta del proyecto (Node, motor y plantillas son portables).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .util import CACHE, ROOT, STATE, info, read_json, warn, write_json

GRAPHICS = ROOT / "assets" / "graphics"
HF_DIR = GRAPHICS / "hf"
COMPOSITIONS = HF_DIR / "compositions"
MANIFEST = HF_DIR / "templates.json"
ROTATION = STATE / "graphics.json"
GRAPHICS_CACHE = CACHE / "graphics"

TOOLS = ROOT / "tools"
HF_CLI = TOOLS / "hyperframes" / "node_modules" / "hyperframes" / "dist" / "cli.js"
FFMPEG_BIN = TOOLS / "ffmpeg" / "bin"
MANAGED_CHROME = TOOLS / "browser" / "chrome" / "chrome.exe"
MANAGED_HEADLESS = TOOLS / "browser" / "chrome-headless-shell" / "chrome-headless-shell.exe"
CHROME_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
]

# Lo que se puede instalar desde el catálogo público de HyperFrames.
SUGGESTED = [
    "aurora", "mesh-gradient", "plasma", "flowing-gradient", "fractal-noise",
    "godrays", "voronoi", "blob", "stripes", "swirl", "ripples", "marble",
    "strands", "worley-noise", "sun-burst",
]

_TITLES = {
    "aurora": "Aurora",
    "mesh-gradient": "Malla de color",
    "plasma": "Plasma",
    "flowing-gradient": "Degradado en flujo",
    "fractal-noise": "Ruido fractal",
    "godrays": "Rayos de luz",
    "voronoi": "Celdas Voronoi",
    "blob": "Manchas orgánicas",
    "stripes": "Rayas",
}


def node_exe() -> Path | None:
    for candidate in sorted(TOOLS.glob("node/*/node.exe")):
        if candidate.exists():
            return candidate
    return None


def browser_exe() -> Path | None:
    configured = os.environ.get("HYPERFRAMES_BROWSER_PATH", "").strip()
    if configured and Path(configured).exists():
        return Path(configured)
    # Navegador propio dentro de la carpeta: no depende de Chrome ni Edge.
    # Primero el Chrome completo (usa GPU y va mucho más rápido); después el
    # headless shell, que funciona en cualquier PC pero renderiza lento.
    if MANAGED_CHROME.exists():
        return MANAGED_CHROME
    if MANAGED_HEADLESS.exists():
        return MANAGED_HEADLESS
    for candidate in CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def available() -> bool:
    return bool(node_exe() and HF_CLI.exists() and (FFMPEG_BIN / "ffmpeg.exe").exists())


def templates() -> list[dict]:
    """Plantillas instaladas (id, título y etiquetas)."""
    data = read_json(MANIFEST, None)
    if isinstance(data, dict):
        items = data.get("templates") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    known = {str(item.get("id")) for item in items if isinstance(item, dict)}
    extra = []
    if COMPOSITIONS.exists():
        for path in sorted(COMPOSITIONS.glob("*.html")):
            if path.stem not in known:
                extra.append({"id": path.stem, "title": _TITLES.get(path.stem, path.stem)})
    return [item for item in items if isinstance(item, dict) and item.get("id")] + extra


def _env() -> dict:
    env = os.environ.copy()
    node = node_exe()
    parts = []
    if node:
        parts.append(str(node.parent))
    parts.append(str(FFMPEG_BIN))
    parts.append(env.get("PATH", ""))
    env["PATH"] = os.pathsep.join(part for part in parts if part)
    browser = browser_exe()
    if browser:
        env["HYPERFRAMES_BROWSER_PATH"] = str(browser)
    env.setdefault("HYPERFRAMES_TELEMETRY", "0")
    return env


def _run(args: list[str], timeout: float = 1800, cwd: Path | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd or (HF_DIR if HF_DIR.exists() else ROOT)),
        env=_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    output = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    return proc.returncode, output.strip()


def _target(template: str, seconds: float, quality: str) -> Path | None:
    source = COMPOSITIONS / f"{template}.html"
    if not source.exists():
        return None
    digest = hashlib.sha1(
        source.read_bytes() + f"{seconds}|{quality}|30|portrait".encode()
    ).hexdigest()[:10]
    return GRAPHICS_CACHE / f"{template}-{int(seconds)}s-{digest}.mp4"


def cached(template: str, seconds: float = 10.0, quality: str = "looks") -> Path | None:
    """Clip ya renderizado (o None si hay que renderizarlo)."""
    target = _target(template, seconds, quality)
    if target and target.exists() and target.stat().st_size > 40_000:
        return target
    return None


def render(template: str, seconds: float = 10.0, quality: str = "looks",
           force: bool = False) -> Path | None:
    """Renderiza una plantilla a MP4 vertical y la cachea."""
    source = COMPOSITIONS / f"{template}.html"
    node = node_exe()
    if not source.exists() or not node:
        warn(f"Plantilla de gráficas no disponible: {template}")
        return None
    GRAPHICS_CACHE.mkdir(parents=True, exist_ok=True)
    target = _target(template, seconds, quality)
    if target is None:
        return None
    if target.exists() and target.stat().st_size > 40_000 and not force:
        return target

    args = [
        str(node), str(HF_CLI), "render", str(HF_DIR),
        "-c", f"compositions/{template}.html",
        "--quality", quality, "--fps", "30", "--format", "mp4",
        "--output", str(target), "--quiet",
    ]
    info(f"Gráficas: renderizando «{template}» con HyperFrames (~1 min)…")
    try:
        code, output = _run(args)
    except subprocess.TimeoutExpired:
        warn(f"HyperFrames tardó demasiado con «{template}».")
        return None
    if code != 0 or not target.exists() or target.stat().st_size < 40_000:
        tail = "\n".join(output.splitlines()[-8:])
        warn(f"No pude renderizar «{template}» ({code}). {tail[:400]}")
        return None
    return target


def choose(count: int, duration: float = 10.0, quality: str = "looks") -> list[tuple[str, float]]:
    """Elige plantillas distintas para los tramos del vídeo, rotando.

    Prefiere las plantillas que ya están en caché para no tener que renderizar
    de nuevo (renderizar una plantilla tarda ~1,5 min). Las nuevas se dejan
    para cuando haya que cambiar de look.
    """
    items = templates()
    if not items:
        return []
    ids = [str(item["id"]) for item in items]
    state = read_json(ROTATION, {}) or {}
    last = state.get("last")
    start = ids.index(last) + 1 if last in ids else 0
    ordered = [ids[(start + index) % len(ids)] for index in range(len(ids))]

    cached = [name for name in ordered if _target(name, duration, quality)
              and _target(name, duration, quality).exists()
              and _target(name, duration, quality).stat().st_size > 40_000]
    fresh = [name for name in ordered if name not in cached]
    picked = (cached + fresh)[:max(1, count)]
    write_json(ROTATION, {"last": picked[-1]})
    return [(name, duration) for name in picked]


def prepare_backgrounds(cfg: dict, lengths: list[float], work: Path) -> list[Path]:
    """Renderiza (o reutiliza) un fondo HyperFrames por tramo."""
    from .media import _normalize

    work = Path(work)
    quality = get_quality(cfg)
    clip_seconds = seconds(cfg)
    count = len(lengths)
    picks = choose(count, clip_seconds, quality)
    if not picks:
        return []
    outputs: list[Path] = []
    for index, (template, clip_seconds) in enumerate(picks):
        if index >= len(lengths):
            break
        clip = render(template, seconds=clip_seconds, quality=quality)
        if not clip:
            return []
        out = work / f"bg{index}.mp4"
        _normalize(clip, out, lengths[index], cfg)
        outputs.append(out)
    return outputs


def get_quality(cfg: dict) -> str:
    from .config import get

    quality = str(get(cfg, "graphics.quality", "looks")).lower()
    return quality if quality in {"draft", "looks", "delivery"} else "looks"


def seconds(cfg: dict) -> float:
    from .config import get

    try:
        value = float(get(cfg, "graphics.seconds", 10) or 10)
    except (TypeError, ValueError):
        value = 10.0
    return min(20.0, max(4.0, value))


def install(name: str, force: bool = False) -> str:
    """Descarga una plantilla del catálogo de HyperFrames y la deja lista."""
    node = node_exe()
    if not node or not HF_CLI.exists():
        raise RuntimeError("HyperFrames no está instalado. Ejecuta setup.cmd.")
    name = name.strip().lower()
    if not name.replace("-", "").isalnum():
        raise ValueError("Nombre de plantilla no válido.")
    stage = CACHE / "hf-install"
    stage.mkdir(parents=True, exist_ok=True)
    project = stage / "project"
    if not (project / "index.html").exists():
        code, output = _run([
            str(node), str(HF_CLI), "init", "project", "--example", "blank",
            "--resolution", "portrait", "--non-interactive", "--skip-skills",
        ], timeout=300, cwd=stage)
        if code != 0:
            raise RuntimeError(f"No pude preparar el proyecto temporal. {output[-300:]}")

    add_args = [
        str(node), str(HF_CLI), "add", name, "--dir", str(project),
        "--no-clipboard", "--json",
    ]
    if force:
        add_args.append("--force")
    code, output = _run(add_args, timeout=300)
    if code != 0:
        raise RuntimeError(f"El catálogo no tiene «{name}». {output[-200:]}")

    found = list(project.glob(f"compositions/{name}.html"))
    if not found:
        found = [p for p in project.glob("compositions/**/*.html")
                 if p.stem == name]
    if not found:
        raise RuntimeError(f"«{name}» no es una composición completa (es un trozo). "
                           "Prueba con: aurora, plasma, mesh-gradient…")
    raw = found[0].read_text(encoding="utf-8")
    if "data-composition-id" not in raw or "data-duration" not in raw:
        raise RuntimeError(f"«{name}» es un componente, no una plantilla completa.")

    adapted = _portrait(raw)
    target = COMPOSITIONS / f"{name}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(adapted, encoding="utf-8")
    lib = found[0].parent / "lib"
    if lib.exists():
        shutil.copytree(lib, COMPOSITIONS / "lib", dirs_exist_ok=True)

    items = templates()
    if not any(str(item.get("id")) == name for item in items):
        items.append({"id": name, "title": _TITLES.get(name, name.replace("-", " ").title())})
        write_json(MANIFEST, {"templates": items})
    return name


def _portrait(html: str) -> str:
    replacements = [
        ("width: 1920px", "width: 1080px"),
        ("height: 1080px", "height: 1920px"),
        ('width="1920"', 'width="1080"'),
        ('height="1080"', 'height="1920"'),
        ('data-width="1920"', 'data-width="1080"'),
        ('data-height="1080"', 'data-height="1920"'),
    ]
    for old, new in replacements:
        html = html.replace(old, new)
    html = html.replace('data-duration="2"', 'data-duration="10"')
    html = html.replace('data-duration="2.5"', 'data-duration="10"')
    return html


def status() -> dict:
    items = templates()
    return {
        "engine": "HyperFrames",
        "available": available(),
        "node": bool(node_exe()),
        "browser": str(browser_exe() or ""),
        "templates": len(items),
        "names": [item.get("id") for item in items],
        "items": [{"id": item.get("id"), "title": item.get("title") or item.get("id")} for item in items],
        "suggested": list(SUGGESTED),
    }
