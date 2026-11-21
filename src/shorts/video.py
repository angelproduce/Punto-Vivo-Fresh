"""Montaje final con FFmpeg: fondos + voz + subtítulos + barra + portada."""
from __future__ import annotations

import shutil
from pathlib import Path

from .config import get, path_of
from .util import (MUSIC_DIR, SFX_DIR, err, ffmpeg, font_file, info, probe_duration, stamp, warn)

AUDIO_SUFFIX = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _wrap(text: str, width: int = 24, max_lines: int = 6) -> str:
    lines: list[str] = []
    current = ""
    for word in str(text or "").split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:max_lines])


def _find_music(cfg: dict) -> Path | None:
    configured = str(get(cfg, "video.music", "") or "").strip()
    if configured:
        path = path_of(configured)
        if path.exists():
            return path
        warn("No encuentro la música configurada; sigo sin música.")
    if not MUSIC_DIR.exists():
        return None
    files = [c for c in MUSIC_DIR.iterdir()
             if c.suffix.lower() in AUDIO_SUFFIX and c.stat().st_size > 10000]
    if not files:
        return None
    style = str(cfg.get("_style", "") or "")
    title = str(cfg.get("_title", "") or "").lower()
    theme_terms = ("gta", "grand theft", "trap", "hype", "rap", "rock", "metal")
    news_terms = ("news", "ambient", "corporate", "calm", "documentary",
                  "documental", "piano", "suspense", "tension")

    def score(p: Path) -> int:
        name = p.stem.lower()
        s = 0
        if "gta" in title and "gta" in name:
            s += 10
        if style in ("news", "periodistico", "documental", "stats", "clean"):
            if any(k in name for k in news_terms):
                s += 6
        if style == "hype" and any(k in name for k in ("gta", "energy", "hype", "trap", "bass")):
            s += 6
        if style in ("cinematic", "cinematografico") and any(
                k in name for k in ("cinematic", "orchestral", "epic", "piano")):
            s += 6
        if style == "trailer" and any(
                k in name for k in ("trailer", "epic", "cinematic", "orchestral",
                                    "dramatic", "tension")):
            s += 8
        if style == "vibrant" and any(k in name for k in ("pop", "upbeat", "dance", "funk")):
            s += 6
        if style == "velocity" and any(k in name for k in ("fast", "electro", "edm", "drum")):
            s += 6
        # Una sintonía de otro tema (p. ej. GTA) no sirve para una noticia.
        if any(term in name for term in theme_terms) and not any(
                term in title for term in theme_terms):
            s -= 12
        return s

    ranked = sorted(files, key=lambda p: (-score(p), p.name))
    if ranked:
        # La carpeta `assets/music` es la fuente principal: si hay pistas, se
        # usa la mejor rankeada aunque el estilo no coincida. La base ambiental
        # generada queda solo para cuando la carpeta está vacía.
        chosen = ranked[0]
        info(f"Música elegida: {chosen.name} (estilo {style or 'auto'})")
        return chosen
    return None


def _ensure_ambient_music(work: Path, duration: float) -> Path | None:
    """Base musical neutra (drones + aire) para noticias y documentales.

    Se sintetiza con FFmpeg: sin copyright y sin usar música de otro tema.
    """
    target = work / "music.m4a"
    if target.exists() and target.stat().st_size > 10000:
        return target
    length = max(8.0, float(duration) + 1.0)
    fade_out = max(1.0, length - 2.5)
    try:
        ffmpeg([
            "-f", "lavfi", "-i",
            f"sine=frequency=110:sample_rate=48000:duration={length:.2f}",
            "-f", "lavfi", "-i",
            f"sine=frequency=164.81:sample_rate=48000:duration={length:.2f}",
            "-f", "lavfi", "-i",
            f"sine=frequency=220:sample_rate=48000:duration={length:.2f}",
            "-f", "lavfi", "-i",
            f"anoisesrc=color=pink:amplitude=0.18:sample_rate=48000:duration={length:.2f}",
            "-f", "lavfi", "-i",
            f"sine=frequency=261.63:sample_rate=48000:duration={length:.2f}",
            "-f", "lavfi", "-i",
            f"sine=frequency=329.63:sample_rate=48000:duration={length:.2f}",
            "-filter_complex",
            "[0:a]volume=0.22[a0];[1:a]volume=0.13[a1];"
            "[2:a]volume=0.06[a2];[3:a]lowpass=f=650,volume=0.05[a3];"
            # Presencia en medios: sin esto la base (110-220 Hz) casi no se oye
            # en el altavoz de un móvil aunque el nivel sea correcto.
            "[4:a]lowpass=f=3000,volume=0.05[a4];"
            "[5:a]lowpass=f=3000,volume=0.035[a5];"
            # Sin `amix` normalizado: dividir entre 4 dejaba la base casi
            # inaudible. Luego se fija un nivel estable de fondo (-18 LUFS).
            "[a0][a1][a2][a3][a4][a5]amix=inputs=6:duration=first:normalize=0,"
            "volume=0.85,tremolo=f=0.11:d=0.28,lowpass=f=2400,"
            "loudnorm=I=-18:TP=-4:LRA=6,"
            f"afade=t=in:st=0:d=1.6,afade=t=out:st={fade_out:.2f}:d=2.2[out]",
            "-map", "[out]", "-c:a", "aac", "-b:a", "160k",
            "-ar", "48000", "-ac", "2", target.name,
        ], cwd=work, timeout=300)
        if target.exists() and target.stat().st_size > 10000:
            info("Base musical neutra generada (sin copyright).")
            return target
    except Exception as exc:
        warn(f"No pude generar la base musical ({str(exc)[:100]}).")
    return None


def _graph(cfg: dict, clips: int, duration: float, hook_sec: float,
           has_music: bool, animated: bool, has_subs: bool, brand: bool,
           style: str = "clean", overlays: list | None = None,
           overlay_start_idx: int = 0, segments: list[float] | None = None) -> str:
    width = int(get(cfg, "video.width", 1080))
    height = int(get(cfg, "video.height", 1920))
    fps = int(get(cfg, "video.fps", 30))
    bar_y = height - 104
    bar_x = 90
    bar_w = width - 180
    overlays = overlays or []

    parts: list[str] = []
    for index in range(clips):
        parts.append(
            f"[{index}:v]setsar=1,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={fps}[v{index}]"
        )

    use_transitions = bool(animated) and clips > 1 and style in (
        "hype", "velocity", "vibrant", "cinematografico", "cinematic", "news",
        "trailer", "videojuegos", "tecnologico", "periodistico", "documental",
        "clean", "stats")
    trans_dur = float(get(cfg, "video.transition_seconds", 0.45) or 0.45)
    if segments and len(segments) == clips:
        segs = [max(0.4, float(s)) for s in segments]
        total_seg = sum(segs)
        if total_seg > 0 and abs(total_seg - duration) > 0.02:
            segs[-1] = max(0.4, segs[-1] + (duration - total_seg))
    else:
        segs = [duration / max(1, clips)] * clips
    if use_transitions:
        import random
        pool = {
            "hype": ["rectcrop", "distance", "hlslice", "hrslice", "vuslice", "vdslice"],
            "videojuegos": ["rectcrop", "distance", "hlslice", "slideleft", "slideright"],
            "velocity": ["slideleft", "slideright", "slideup", "slidedown"],
            "vibrant": ["circlecrop", "radial", "fade", "smoothleft"],
            "tecnologico": ["fade", "slideleft", "circleopen"],
            "cinematic": ["fade", "fadeblack", "fadewhite"],
            "cinematografico": ["fade", "fadeblack", "fadewhite"],
            "news": ["fade", "fadeblack", "slideleft"],
            "periodistico": ["fade", "fadeblack", "slideleft"],
            "trailer": ["fade", "fadeblack", "fadewhite"],
        }.get(style, ["fade"])
        rng = random.Random(hash(style + str(duration) + str(clips)))
        cur = "[v0]"
        for i in range(1, clips):
            ttype = rng.choice(pool)
            # Cada clip dura su tramo + trans_dur, así el corte cae exacto
            # al terminar la narración de su escena.
            offset = max(0.15, sum(segs[:i]))
            newlabel = f"[xf{i}]"
            parts.append(f"{cur}[v{i}]xfade=transition={ttype}:duration={trans_dur:.2f}:offset={offset:.2f}{newlabel}")
            cur = newlabel
        parts.append(f"{cur}trim=duration={duration:.2f},setpts=PTS-STARTPTS,fps={fps}[cat]")
    elif clips > 1:
        labels = "".join(f"[v{i}]" for i in range(clips))
        parts.append(f"{labels}concat=n={clips}:v=1:a=0[catraw]")
        parts.append(f"[catraw]trim=duration={duration:.2f},setpts=PTS-STARTPTS,fps={fps}[cat]")
    else:
        parts.append(f"[v0]trim=duration={duration:.2f},setpts=PTS-STARTPTS,fps={fps}[cat]")

    grade = {
        "hype": "eq=saturation=1.4:contrast=1.2:brightness=0.03",
        "velocity": "eq=saturation=1.25:contrast=1.18:brightness=0.02",
        "vibrant": "eq=saturation=1.55:contrast=1.22:brightness=0.04",
        "cinematic": "eq=saturation=0.85:contrast=1.12:brightness=-0.02",
        "cinematografico": "eq=saturation=0.85:contrast=1.12:brightness=-0.02",
        "news": "eq=saturation=1.0:contrast=1.02:brightness=0.0",
        "periodistico": "eq=saturation=1.0:contrast=1.02:brightness=0.0",
        "clean": "eq=saturation=1.0:contrast=1.05",
        "documental": "eq=saturation=1.0:contrast=1.05",
        "tecnologico": "eq=saturation=1.15:contrast=1.08:brightness=0.01",
        "videojuegos": "eq=saturation=1.45:contrast=1.2:brightness=0.03",
        "stats": "eq=saturation=1.05:contrast=1.08",
        "trailer": "eq=saturation=0.8:contrast=1.18:brightness=-0.04:gamma=0.95",
    }.get(style, "eq=saturation=1.05:contrast=1.05")
    vignette = "vignette=PI/4.0" if style in ("hype", "cinematic", "cinematografico", "vibrant", "trailer", "videojuegos") else ""
    grain = "noise=alls=3:allf=t+u" if style in ("cinematic", "cinematografico", "hype", "trailer") else ""
    chain = grade
    if vignette: chain += "," + vignette
    if grain: chain += "," + grain
    # El zoom dinámico se aplica por plano en media._normalize, no al vídeo
    # completo (un zoom global se notaba como deriva y desafinaba los cortes).
    if style in ("hype", "trailer", "velocity", "videojuegos"):
        chain += ",tblend=all_mode=average"
    parts.append(f"[cat]{chain}[base]")

    bar = f"[base]drawbox=x={bar_x}:y={bar_y}:w={bar_w}:h=8:color=0xFFFFFF@0.18:t=fill"
    if animated:
        bar += f",drawbox=x={bar_x}:y={bar_y}:w='{bar_w}*min(1,t/{duration:.2f})':h=8:color=0x22D3EE@0.95:t=fill"
    parts.append(bar + "[bar]")

    # El título ya no es un rectángulo negro con drawtext: lo dibuja el motor
    # gráfico (SVG/Pillow) como overlay moderno sobre el fragmento real.
    stage = "[bar]"
    if brand:
        parts.append(
            f"{stage}drawtext=textfile='brand.txt':fontfile='font.ttf':"
            f"fontsize=34:fontcolor=white@0.90:borderw=2:bordercolor=black@0.65:"
            f"x=54:y=54[branded]"
        )
        stage = "[branded]"
    parts.append(
        f"{stage}drawtext=textfile='timestamp.txt':fontfile='font.ttf':"
        f"fontsize=26:fontcolor=white@0.65:borderw=2:bordercolor=black@0.65:"
        f"x=54:y=h-54[timestamped]"
    )
    stage = "[timestamped]"
    parts.append(
        f"{stage}drawtext=textfile='closing.txt':fontfile='font.ttf':"
        f"fontsize=46:fontcolor=white:borderw=3:bordercolor=black@0.80:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='gte(t,{max(1.0, duration - 4.0):.2f})'[vbase]"
    )

    # Composite overlays (images with alpha)
    vcur = "[vbase]"
    for oi, ov in enumerate(overlays):
        if ov.get("kind") != "image":
            continue
        inp = overlay_start_idx + oi
        start = float(ov.get("start", 0.0))
        dur = ov.get("duration") or max(1.0, duration - start)
        end = min(duration, start + dur)
        fade_in = float(ov.get("fade_in", 0.25))
        fade_out = float(ov.get("fade_out", 0.25))
        ovl = f"[ov{oi}]"
        chain = f"[{inp}:v]scale={width}:{height},format=rgba"
        if fade_in > 0:
            chain += f",fade=t=in:st={start:.2f}:d={fade_in:.2f}:alpha=1"
        if fade_out > 0:
            fo_st = max(start, end - fade_out)
            chain += f",fade=t=out:st={fo_st:.2f}:d={fade_out:.2f}:alpha=1"
        parts.append(chain + ovl)
        vnext = f"[vov{oi}]"
        parts.append(f"{vcur}{ovl}overlay=0:0:enable='between(t,{start:.2f},{end:.2f})':format=auto{vnext}")
        vcur = vnext

    if has_subs:
        parts.append(f"{vcur}ass=filename=subs.ass[vout]")
    else:
        parts.append(f"{vcur}null[vout]")

    # audio
    # La voz se normaliza a un nivel alto y el resto se suma SIN repartir la
    # ganancia entre las pistas: con `amix` normalizado, cada pista extra
    # bajaba la voz varios dB (la causa de que sonara baja).
    lufs = float(get(cfg, "video.voice_lufs", -13.0) or -13.0)
    true_peak = float(get(cfg, "video.voice_tp", -1.0) or -1.0)
    voice = (f"[{clips}:a]loudnorm=I={lufs:g}:TP={true_peak:g}:LRA=9,"
             "aresample=48000,pan=stereo|c0=c0|c1=c0")
    limiter = "alimiter=limit=0.80:level=0"
    if has_music:
        parts.append(voice + "[a]")
        # La música se normaliza a un nivel de fondo constante antes de aplicar
        # la ganancia final: así una pista externa floja también se escucha.
        # El pico del fondo queda bajo (-4) para poder subir la ganancia sin
        # que el limitador final tenga que trabajar de más.
        music_db = float(get(cfg, "video.music_db", 3.0))
        parts.append(f"[{clips + 1}:a]aresample=48000,pan=stereo|c0=c0|c1=c0,"
                     "loudnorm=I=-18:TP=-4:LRA=6,"
                     f"volume={music_db:.1f}dB[bed]")
        parts.append("[a]asplit=2[voice1][voice2]")
        # Ducking suave: la música baja lo justo para que la voz se entienda,
        # pero sigue presente. Antes el umbral (0.08) hundía la música ~11 dB
        # durante toda la narración, que es lo que la hacía casi inaudible.
        parts.append("[bed][voice1]sidechaincompress=threshold=0.22:ratio=1.5:"
                     "attack=15:release=350:makeup=1[ducked]")
        # Primeros segundos: la música entra con un realce muy leve (+1,5 dB
        # por defecto), fundido corto de entrada y bajada suave al empezar la
        # narración. El realce se aplica después del ducking, pero se mantiene
        # por debajo de la voz para no taparla.
        intro_boost = float(get(cfg, "video.music_intro_db", 1.5) or 0.0)
        intro_sec = max(0.0, min(float(hook_sec or 0.0), duration))
        fade = max(0.1, float(get(cfg, "video.music_intro_fade", 0.7) or 0.7))
        fade_in = max(0.05, float(get(cfg, "video.music_fade_in", 0.3) or 0.3))
        music_out = "[ducked]"
        if intro_boost > 0.05 and intro_sec > 0.2:
            gain = 10 ** (intro_boost / 20.0)
            expr = (f"min(1,t/{fade_in:.3f})*(1+({gain:.4f}-1)*"
                    f"max(0,min(1,({intro_sec + fade:.3f}-t)/{fade:.3f})))")
            parts.append(f"[ducked]volume='{expr}':eval=frame[musicintro]")
            music_out = "[musicintro]"
        parts.append(f"[voice2]{music_out}amix=inputs=2:duration=first:dropout_transition=0:normalize=0,{limiter}[aout]")
    else:
        parts.append(voice + f",{limiter}[aout]")

    return ";\n".join(parts)


def _render(cfg: dict, work: Path, duration: float, hook_sec: float,
            has_music: bool, has_subs: bool, brand: bool, clips: int,
            out: Path, animated: bool, style: str = "clean",
            overlays: list | None = None,
            segments: list[float] | None = None) -> Path:
    width = int(get(cfg, "video.width", 1080))
    height = int(get(cfg, "video.height", 1920))
    fps = int(get(cfg, "video.fps", 30))
    crf = int(get(cfg, "video.crf", 20) or 20)
    preset = str(get(cfg, "video.preset", "veryfast"))
    overlays = overlays or []

    inputs: list[str] = []
    for index in range(clips):
        inputs += ["-i", f"bg{index}.mp4"]
    inputs += ["-i", "voice.wav"]
    if has_music:
        inputs += ["-stream_loop", "-1", "-i", "music" + _music_suffix(work)]

    # overlay inputs come after music (or after voice if no music)
    overlay_start_idx = clips + 1 + (1 if has_music else 0)
    ov_data = []
    for oi, ov in enumerate(overlays):
        kind = getattr(ov, "kind", "image")
        if kind == "image":
            start_ov = float(getattr(ov, "start", 0.0) or 0.0)
            dur_ov = float(getattr(ov, "duration", None) or 3.0)
            end_ov = min(duration, start_ov + dur_ov + 0.5)
            inputs += ["-loop", "1", "-t", f"{end_ov:.2f}", "-i", f"ov_{oi}.png"]
            ov_data.append({"kind": "image", "start": getattr(ov, "start", 0.0), "duration": getattr(ov, "duration", None),
                            "fade_in": getattr(ov, "fade_in", 0.25), "fade_out": getattr(ov, "fade_out", 0.25)})
        # video overlays can be added later

    graph = _graph(cfg, clips, duration, hook_sec, has_music, animated, has_subs, brand, style,
                   overlays=ov_data, overlay_start_idx=overlay_start_idx,
                   segments=segments)

    ffmpeg([
        *inputs,
        "-filter_complex", graph,
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", str(fps * 2),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out.name,
    ], cwd=work, timeout=1800)
    return out


def _music_suffix(work: Path) -> str:
    for item in work.iterdir():
        if item.name.startswith("music."):
            return item.suffix
    return ".mp3"


def _sfx_files() -> list[Path]:
    if not SFX_DIR.exists():
        return []
    return [p for p in sorted(SFX_DIR.iterdir())
            if p.suffix.lower() in AUDIO_SUFFIX and p.stat().st_size > 1024]


def _ensure_whoosh(work: Path) -> Path | None:
    """Genera un whoosh sutil si no hay efectos en assets/sfx."""
    target = work / "whoosh.wav"
    if target.exists() and target.stat().st_size > 1000:
        return target
    try:
        ffmpeg([
            "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.5:duration=0.5",
            "-af", "highpass=f=500,lowpass=f=7000,"
                   "afade=t=in:d=0.06,afade=t=out:st=0.16:d=0.34",
            "-c:a", "pcm_s16le", target.name,
        ], cwd=work)
        if target.exists() and target.stat().st_size > 1000:
            return target
    except Exception as exc:
        warn(f"No pude generar el efecto de transición ({str(exc)[:80]}).")
    return None


def _scene_boundaries(cfg: dict, duration: float) -> list[float]:
    """Momentos exactos donde cambia la escena (incluye el fin de la intro)."""
    segments = cfg.get("_segments") if isinstance(cfg, dict) else None
    if segments and len(segments) > 1:
        out: list[float] = []
        clock = 0.0
        for length in segments[:-1]:
            clock += float(length)
            if 0.4 < clock < duration - 0.4:
                out.append(clock)
        if out:
            return out
    count = max(2, min(6, int(duration / 18)))
    return [duration * index / count for index in range(1, count)]


def _mix_sfx(cfg: dict, work: Path, duration: float) -> None:
    """Mezcla efectos en los cambios de escena, no en momentos aleatorios."""
    if not bool(get(cfg, "video.sfx_enabled", True)):
        return
    boundaries = _scene_boundaries(cfg, duration)
    if not boundaries:
        return
    files = _sfx_files()
    if files:
        chosen = [files[i % len(files)] for i in range(len(boundaries))]
    else:
        whoosh = _ensure_whoosh(work)
        if not whoosh:
            return
        chosen = [whoosh] * len(boundaries)
    volume_db = float(get(cfg, "video.sfx_db", -20) or -20)

    inputs = ["-i", "final.mp4"]
    for sfx in chosen:
        inputs += ["-i", str(sfx)]

    filters: list[str] = []
    labels: list[str] = []
    for idx, (sfx, delay) in enumerate(zip(chosen, boundaries)):
        ms = int(delay * 1000)
        label = f"s{idx}"
        filters.append(
            f"[{idx + 1}:a]adelay={ms}|{ms},volume={volume_db}dB,aresample=48000[{label}]"
        )
        labels.append(f"[{label}]")

    all_inputs = "[0:a]" + "".join(labels)
    n = 1 + len(labels)
    filters.append(f"{all_inputs}amix=inputs={n}:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.84:level=0[aout]")
    graph = ";\n".join(filters)

    out_tmp = work / "final_sfx.mp4"
    try:
        ffmpeg([
            *inputs,
            "-filter_complex", graph,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out_tmp.name,
        ], cwd=work, timeout=600)
    except Exception as exc:
        warn(f"No se pudieron mezclar los efectos de sonido ({str(exc)[:120]}).")
        out_tmp.unlink(missing_ok=True)
        return
    if out_tmp.exists() and out_tmp.stat().st_size > 20000:
        out_tmp.replace(work / "final.mp4")
        info(f"Efectos añadidos en {len(chosen)} cambio(s) de escena.")
    else:
        warn("Los efectos de sonido no se aplicaron (salida inválida).")
        out_tmp.unlink(missing_ok=True)


def _ensure_title_overlay(cfg: dict, script, work: Path, overlays: list,
                          hook_sec: float) -> list:
    """Si el motor SVG no generó tarjeta de título, la dibuja con Pillow.

    Así el título nunca vuelve a ser un rectángulo negro con drawtext, ni
    siquiera cuando el navegador headless no está disponible.
    """
    labels = {str(getattr(ov, "label", "") or "") for ov in overlays}
    if "title_card" in labels or hook_sec <= 0.5:
        return overlays
    try:
        from .graphics_engines import OverlayAsset
        from .graphics_engines.svg_engine import make_title_png

        width = int(get(cfg, "video.width", 1080))
        height = int(get(cfg, "video.height", 1920))
        style_name = str(cfg.get("_gfx_style") or "periodistico")
        target = work / "fallback_title.png"
        subtitle = getattr(script, "hook", "") or ""
        make_title_png(str(getattr(script, "title", "") or ""), subtitle,
                       style_name, width, height, target)
        overlays = list(overlays) + [OverlayAsset(
            path=target, kind="image", start=0.15,
            duration=max(2.0, hook_sec - 0.35),
            fade_in=0.35, fade_out=0.45, label="title_card",
        )]
        info("Tarjeta de título moderna añadida (Pillow).")
    except Exception as exc:
        warn(f"No pude crear la tarjeta de título ({str(exc)[:100]}).")
    return overlays


def render(cfg: dict, script, voice: Path, subs: Path | None, backgrounds: list[Path],
           work: Path, overlays: list | None = None,
           segments: list[float] | None = None) -> tuple[Path, Path]:
    work = Path(work)
    duration = probe_duration(voice)
    if duration < 2:
        raise RuntimeError("La voz generada es demasiado corta.")

    for index, clip in enumerate(backgrounds):
        target = work / f"bg{index}.mp4"
        if Path(clip).resolve() != target.resolve():
            shutil.copyfile(clip, target)

    # La misma intro que usó media: así título, voz y cortes cuadran.
    hook_sec = float(cfg.get("_hook_seconds")
                     or get(cfg, "video.hook_seconds", 4.0) or 4.0)
    hook_sec = max(0.0, min(hook_sec, max(0.0, duration - 1.5)))
    segments = list(segments or [])
    if len(segments) != len(backgrounds):
        segments = None
    overlays = _ensure_title_overlay(cfg, script, work, list(overlays or []), hook_sec)

    # Marco decorativo elegido a mano en Ajustes. Va al final para quedar por
    # encima del resto, pero bajo los subtítulos (que se añaden después).
    frame_name = str(get(cfg, "video.frame_template", "") or "").strip()
    if frame_name:
        from .frames import frame_label, frame_overlay

        frame = frame_overlay(frame_name, duration)
        if frame is not None:
            overlays.append(frame)
            info(f"Marco del vídeo: {frame_label(frame_name)}")
        else:
            warn(f"Marco «{frame_name}» activo pero falta su PNG; sigo sin marco.")

    closing = str(get(cfg, "project.closing", "") or "¿Te parece? Comenta.").strip()
    (work / "closing.txt").write_text(_wrap(closing, 28, 3), encoding="utf-8")

    brand = str(get(cfg, "project.brand", "") or "").strip()
    if brand:
        (work / "brand.txt").write_text(brand, encoding="utf-8")

    (work / "timestamp.txt").write_text(stamp(), encoding="utf-8")

    font = font_file(bold=True)
    if font:
        shutil.copyfile(font, work / "font.ttf")
    else:
        raise RuntimeError("No encuentro una fuente para los textos. Ejecuta setup.cmd otra vez.")

    has_subs = bool(subs and Path(subs).exists())
    if has_subs and Path(subs).resolve() != (work / "subs.ass").resolve():
        shutil.copyfile(subs, work / "subs.ass")

    music = _find_music(cfg)
    if music:
        target = work / ("music" + music.suffix)
        if Path(music).resolve() != target.resolve():
            shutil.copyfile(music, target)
    else:
        # Ninguna pista de assets/music encaja con el tema: base neutra.
        music = _ensure_ambient_music(work, duration)

    valid_overlays = []
    for ov in overlays:
        try:
            src = getattr(ov, "path", None)
            if src and Path(src).exists() and getattr(ov, "kind", "image") == "image":
                index = len(valid_overlays)
                shutil.copyfile(src, work / f"ov_{index}.png")
                valid_overlays.append(ov)
        except Exception as exc:
            warn(f"Overlay descartado: {str(exc)[:80]}")
    overlays = valid_overlays
    if music:
        info(f"Música de fondo: {music.name}")

    style = str(cfg.get("_style", "clean") if isinstance(cfg, dict) else "clean")
    out = work / "final.mp4"
    try:
        _render(cfg, work, duration, hook_sec, bool(music), has_subs, bool(brand),
                len(backgrounds), out, animated=True, style=style,
                overlays=overlays, segments=segments)
    except Exception as exc:
        warn(f"El montaje con animaciones falló ({str(exc)[:120]}). Reintento simplificado.")
        _render(cfg, work, duration, hook_sec, bool(music), has_subs, bool(brand),
                len(backgrounds), out, animated=False, style="clean",
                overlays=overlays, segments=segments)

    if not out.exists() or out.stat().st_size < 20000:
        err("El vídeo salió vacío.")
        raise RuntimeError("No se pudo montar el vídeo.")

    _mix_sfx(cfg, work, duration)

    cover = work / "cover.jpg"
    # Portada con el título visible (no un fotograma negro del inicio).
    moment = min(2.0, max(0.75, duration * 0.02))
    ffmpeg(["-ss", f"{moment:.2f}", "-i", "final.mp4", "-frames:v", "1",
            "-q:v", "3", "cover.jpg"], cwd=work)
    return out, cover
