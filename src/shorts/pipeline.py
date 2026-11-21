"""Orquestador: guion → voz clonada → fondos → subtítulos → vídeo → cola."""
from __future__ import annotations

import re
import shutil
import time
import traceback
from pathlib import Path

from . import captions as captions_mod
from . import media, queue, script_gen, security, tts, video
from .config import get
from .util import (LOGS, WORK, ensure_dirs, err, ffmpeg, fmt_dur, info, ok,
                   probe_duration, shorten, step, warn)


def _style_for(cfg: dict, script) -> str:
    # Usa la detección modular (contenido + configuración) en vez de forzar
    # siempre noticias: un documental debe editarse como documental.
    from .graphics_engines import detect_style
    return detect_style(cfg, script)


def _work_dir(tag: str) -> Path:
    path = WORK / f"{time.strftime('%H%M%S')}-{tag}"
    path.mkdir(parents=True, exist_ok=True)
    return path


_WORD_RE = re.compile(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+", re.UNICODE)


def _scene_timings(script, words: list[dict], duration: float,
                   hook_sec: float) -> tuple[float, list[float]] | None:
    """Corta los planos exactamente donde empieza cada escena narrada.

    Whisper ya nos da el tiempo real de cada palabra; repartimos las palabras
    del texto entre las escenas y usamos esos instantes como cortes. Así el
    vídeo cambia de plano cuando cambia el contenido, no por una estimación.
    """
    scenes = [s for s in (getattr(script, "scenes", []) or [])
              if str(getattr(s, "narration", "") or "").strip()]
    if not scenes or not words:
        return None
    counts = [max(1, len(_WORD_RE.findall(s.narration))) for s in scenes]
    total = sum(counts)
    usable = [w for w in words
              if float(w.get("start", 0.0) or 0.0) >= max(0.0, hook_sec - 0.05)]
    if len(usable) < 3:
        return None

    starts: list[float] = []
    consumed = 0
    for count in counts:
        pos = int(round((consumed / total) * len(usable)))
        pos = max(0, min(len(usable) - 1, pos))
        starts.append(float(usable[pos].get("start", 0.0) or 0.0))
        consumed += count

    intro = max(hook_sec, starts[0] - 0.12)
    starts[0] = intro
    for index in range(1, len(starts)):
        starts[index] = max(starts[index - 1] + 0.6, starts[index] - 0.10)
    if starts[-1] >= duration - 0.4:
        return None

    segments: list[float] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else duration
        segments.append(max(0.4, end - start))
    # Ajuste fino para que la suma sea exactamente la duración real.
    span = duration - intro
    if span <= 0:
        return None
    scale = span / max(0.1, sum(segments))
    segments = [segment * scale for segment in segments]
    return intro, segments


def _intro_track(cfg: dict, work: Path, text: str, hook_sec: float) -> Path:
    """Voz del título encajada en los primeros segundos del vídeo.

    La intro enseña el fragmento real del acontecimiento y, encima, se narra
    el título. La pista resultante dura exactamente `hook_sec`: si la frase
    es más larga se acelera lo justo (sin cortar palabras cuando es posible)
    y si es más corta se rellena con silencio.
    """
    track = work / "intro-voice.wav"
    raw = work / "intro-voice-raw.wav"
    text = " ".join(str(text or "").split())
    spoken = False
    if text:
        try:
            tts.synthesize(cfg, text, raw)
            spoken = raw.exists() and probe_duration(raw) > 0.15
        except Exception as exc:
            warn(f"No pude narrar el título ({str(exc)[:90]}); la intro queda en silencio.")

    if spoken:
        spoken_sec = probe_duration(raw)
        chain: list[str] = []
        if spoken_sec > hook_sec - 0.10:
            tempo = min(1.60, spoken_sec / max(0.40, hook_sec - 0.10))
            if tempo > 1.01:
                chain.append(f"atempo={tempo:.3f}")
        chain += [
            f"apad=whole_dur={hook_sec:.3f}",
            f"atrim=0:{hook_sec:.3f}",
            "afade=t=in:st=0:d=0.05",
            f"afade=t=out:st={max(0.0, hook_sec - 0.15):.3f}:d=0.15",
        ]
        try:
            ffmpeg(["-i", raw.name, "-af", ",".join(chain),
                    "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", track.name],
                   cwd=work)
            if track.exists() and track.stat().st_size > 1024:
                return track
        except Exception as exc:
            warn(f"No pude ajustar la voz del título ({str(exc)[:90]}).")

    # Sin voz de título: se mantiene la intro visual en silencio.
    ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={hook_sec:.3f}",
        "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", track.name,
    ], cwd=work)
    return track


def generate_one(cfg: dict, topic_id: str | None = None,
                 seconds: float | None = None,
                 sources: list[str] | None = None,
                 manual_script: str | None = None,
                 resources: list | None = None) -> Path:
    ensure_dirs()
    started = time.time()

    step("1/6 · Guion")
    script = script_gen.generate_script(cfg, topic_id, seconds,
                                        sources=sources, manual_script=manual_script)
    ok(f"{script.title} · {script.word_count} palabras (~{fmt_dur(script.est_seconds)})"
       f" · {script.provider}")
    if script.sources:
        info("Fuentes: " + ", ".join(script.sources))
    print(f"    {script.full_text}\n")
    if security.is_duplicate(script.title, script.full_text):
        warn("Este guion es muy parecido a uno ya generado (se continúa igualmente).")
    security.mark_seen(script.title, script.full_text)

    work = _work_dir(script.topic_id)
    folder: Path | None = None
    try:
        step("2/6 · Voz clonada")
        voice = work / "voice.wav"

        def progress(index: int, total: int, chunk: str) -> None:
            info(f"Fragmento {index}/{total}: {shorten(chunk, 70)}")

        tts.synthesize(cfg, script.full_text, voice, progress)

        # Intro real: 4 segundos con el título narrado antes del guion.
        hook_sec = max(0.0, min(6.0, float(get(cfg, "video.hook_seconds", 4.0) or 4.0)))
        if hook_sec > 0:
            # La intro narra el título, salvo que el guion pida silencio ("")
            # o traiga su propia frase de entrada.
            intro_text = getattr(script, "intro_text", None)
            text = getattr(script, "title", "") if intro_text is None else intro_text
            intro = _intro_track(cfg, work, text, hook_sec)
            shifted = work / "voice-intro.wav"
            ffmpeg([
                "-i", intro.name,
                "-i", voice.name,
                "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
                "-map", "[out]", "-ac", "1", "-ar", "48000",
                "-c:a", "pcm_s16le", shifted.name,
            ], cwd=work)
            shifted.replace(voice)
            ok(f"Intro de {hook_sec:.1f}s con la narración del título")

        duration = probe_duration(voice)
        limit = script_gen.max_seconds(cfg)
        if duration > limit + 0.4:
            warn(f"La voz dura {fmt_dur(duration)}; la dejo en {fmt_dur(limit)} "
                 "(máximo de 2 minutos).")
            cut = work / "voice-cut.wav"
            fade = max(0.2, limit - 0.35)
            ffmpeg([
                "-i", voice.name, "-t", f"{limit:.2f}",
                "-af", f"afade=t=out:st={fade:.2f}:d=0.35",
                "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", cut.name,
            ], cwd=work)
            cut.replace(voice)
            duration = probe_duration(voice)
        ok(f"{fmt_dur(duration)} de voz generada")

        step("3/6 · Subtítulos")
        words: list[dict] = []
        scene_timings: list[float] | None = None
        subs = None
        if bool(get(cfg, "captions.enabled", True)):
            try:
                words = captions_mod.transcribe(cfg, voice)
            except Exception as exc:
                warn(f"Whisper no disponible ({str(exc)[:90]}); tiempos aproximados.")
                words = []
            if words and hook_sec > 0:
                # Whisper también transcribe el título narrado en la intro: no
                # debe salir como subtítulo ni desplazar los cortes de escena.
                words = [w for w in words
                         if float(w.get("start", 0.0) or 0.0) >= hook_sec - 0.05]
            if words:
                timed = _scene_timings(script, words, duration, hook_sec)
                if timed:
                    hook_sec, scene_timings = timed
                    cfg["_hook_seconds"] = hook_sec
                    info("Cortes de plano sincronizados con la narración (Whisper).")
            subs = captions_mod.build(
                cfg, voice, work / "subs.ass", script.full_text,
                words=words or None, offset=hook_sec,
            )
            if subs:
                ok("Subtítulos con sincronización por palabra")
            else:
                warn("No se pudieron generar los subtítulos")
        else:
            warn("Subtítulos desactivados")

        step("4/6 · Recursos visuales")
        style = _style_for(cfg, script)
        scenes = list(getattr(script, "scenes", []) or [])
        cfg.setdefault("media", {})["query"] = (
            getattr(script, "intro_query", "") or script.title
        )
        cfg.setdefault("media", {})["source"] = "mixed"
        cfg["media"]["max_clips"] = int(get(cfg, "media.max_clips", 10) or 10)
        cfg["_style"] = style
        cfg["_gfx_style"] = style
        cfg["_title"] = script.title
        cfg["_hook_seconds"] = hook_sec
        cfg["_duration"] = duration
        yt_max = get(cfg, "media.youtube_max", 2)
        # GTA 6 se alimenta de tráilers/capturas del propio juego: necesita
        # más descargas de YouTube que un tema de noticias.
        if media.is_gta6(script.title):
            yt_max = max(int(yt_max or 0), 8)
        cfg["_yt_budget"] = max(0, int(2 if yt_max is None else yt_max))
        info(f"Estilo de edición: {style} | Escenas: {len(scenes)} | "
             f"Intro real: {hook_sec:.1f}s")
        for index, scene in enumerate(scenes[:12], 1):
            info(f"  Escena {index}: {shorten(scene.narration, 58)}")
            info(f"           → {scene.visual_query} ({scene.media_type})")
        backgrounds = media.prepare_backgrounds(
            cfg, duration, work, scenes=scenes,
            intro_query=getattr(script, "intro_query", ""),
            intro_seconds=hook_sec,
            scene_timings=scene_timings,
            resources=resources,
        )
        segments = list(cfg.get("_segments") or [])
        ok(f"{len(backgrounds)} clip(s) reales listos · "
           f"{len(segments)} tramo(s) sincronizados")

        step("5/6 · Montaje")
        gfx_plan = None
        try:
            from .graphics_engines import prepare as gfx_prepare
            gfx_plan = gfx_prepare(cfg, script, duration, work)
        except Exception as e:
            warn(f'Graficos modulares: {str(e)[:80]}')
        overlays = gfx_plan.overlays if gfx_plan else []
        if gfx_plan:
            cfg["_gfx_style"] = gfx_plan.style
        final, cover = video.render(
            cfg, script, voice, subs, backgrounds, work,
            overlays=overlays, segments=segments,
        )
        ok(f"Vídeo montado · {final.stat().st_size / (1024 * 1024):.1f} MB")

        step("6/6 · En cola")
        folder = queue.save_short(
            script, final, cover, duration,
            platforms=list(get(cfg, "publish.platforms", []) or []),
        )
        # Guarda la atribución de cada recurso junto al vídeo (licencias).
        credits = work / "media_credits.json"
        if credits.exists():
            shutil.copyfile(credits, folder / "media_credits.json")
        script_gen.mark_used(script.topic_id)
        for extra in getattr(script, "related_ids", []) or []:
            script_gen.mark_used(str(extra))
        ok(f"Guardado en {folder.relative_to(folder.parents[2])}")
        shutil.rmtree(work, ignore_errors=True)
    except Exception as exc:
        err(f"{type(exc).__name__}: {exc}")
        try:
            LOGS.mkdir(parents=True, exist_ok=True)
            with (LOGS / "pipeline.log").open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {script.title}\n")
                handle.write(traceback.format_exc())
        except Exception:
            pass
        print(f"    (los archivos a medias quedan en {work})")
        raise

    print(f"\nTiempo total: {int(time.time() - started)}s")
    assert folder is not None
    return folder


def generate_many(cfg: dict, count: int = 1, topic_id: str | None = None,
                  seconds: float | None = None,
                  sources: list[str] | None = None,
                  manual_script: str | None = None,
                  resources: list | None = None) -> list[Path]:
    total = max(1, int(count))
    folders: list[Path] = []
    for index in range(total):
        if total > 1:
            step(f"Short {index + 1} de {total}")
        # Las fuentes, el guion manual y los recursos propios solo alimentan el
        # primer vídeo; los demás siguen el tema/rotación para no repetir.
        folders.append(generate_one(
            cfg,
            topic_id if index == 0 else None,
            seconds,
            sources=sources if index == 0 else None,
            manual_script=manual_script if index == 0 else None,
            resources=resources if index == 0 else None,
        ))
    return folders
