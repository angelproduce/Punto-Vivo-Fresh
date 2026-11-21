"""Clips automáticos desde vídeos de YouTube o de tu equipo (estilo OpusClip).

Descarga un vídeo (yt-dlp) o usa uno subido, transcribe (Whisper), detecta
los mejores momentos con IA y genera clips verticales con subtítulos.
"""
from __future__ import annotations

import json
import os
import re
import sys
import shutil
import subprocess
from pathlib import Path

from . import captions as captions_mod
from . import queue as queue_mod
from types import SimpleNamespace
from .config import get, load_config
from .util import (CACHE, ensure_dirs, ffmpeg, info, probe_duration, stamp, warn)


CLIPS_DIR = CACHE / "youtube_clips"


def _ytdlp_exe() -> str:
    # yt-dlp instalado como módulo o binario
    try:
        import yt_dlp  # noqa
        return "module"
    except Exception:
        pass
    for name in ("yt-dlp", "yt-dlp.exe", "youtube-dl"):
        if shutil.which(name):
            return name
    raise RuntimeError("yt-dlp no está instalado. Ejecuta: pip install yt-dlp")


def download(url: str, work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    out = work / "source.mp4"
    if out.exists() and out.stat().st_size > 100000:
        info("Vídeo ya descargado, reutilizando.")
        return out
    mode = _ytdlp_exe()
    node = None
    for cand in (r"C:\\Users\\angel\\Documents\\Default Project\\tools\\node\\node-v22.23.4-win-x64\\node.exe",):
        if __import__("os").path.exists(cand):
            node = cand
            break
    cmd = [sys.executable, "-m", "yt_dlp"]
    if node:
        cmd += ["--js-runtimes", f"node:{node}"]
    # Soporte para directos: intentar desde el inicio con reintentos.
    is_live = "youtube.com/live" in url or "/live/" in url
    if is_live:
        cmd += ["--live-from-start", "--retries", "10", "--fragment-retries", "10"]
    cmd += ["--remote-components", "ejs:github", "-f", "best", "-o", str(out), url]
    info(f"Descargando vídeo de YouTube…")
    is_live = "youtube.com/live" in url or "/live/" in url
    max_attempts = 3 if is_live else 1
    last_err = ""
    for attempt in range(max_attempts):
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
            last_err = ""
            break
        except subprocess.CalledProcessError as exc:
            last_err = (exc.stderr or "")[-300:]
            if attempt < max_attempts - 1 and is_live:
                import time as _t
                _t.sleep(5)
                continue
            if "Did not get any data blocks" in last_err or "fragment" in last_err.lower():
                raise RuntimeError(
                    "No se pudo descargar este directo de YouTube. Los directos usan fragmentos que expiran. "
                    "Intenta de nuevo en unos segundos, o usa un video ya finalizado (VOD)."
                )
            raise RuntimeError(f"Error al descargar: {last_err[:200]}")
        except Exception as exc:
            raise RuntimeError(f"Error al descargar: {str(exc)[:200]}")
    if last_err:
        raise RuntimeError(f"Error al descargar: {last_err[:200]}")
    if not out.exists():
        # yt-dlp puede añadir extensión
        candidates = sorted(work.glob("source*"), key=lambda p: p.stat().st_size, reverse=True)
        if candidates:
            candidates[0].replace(out)
    if not out.exists() or out.stat().st_size < 100000:
        raise RuntimeError("No se pudo descargar el vídeo.")
    return out


def use_local_video(path: Path, work: Path) -> Path:
    """Copia un vídeo subido al directorio de trabajo del job.

    La interfaz ya validó que el archivo está dentro de state/resources y que
    es un vídeo legible; aquí solo lo aislamos para que no pueda borrarse
    mientras se cortan los clips.
    """
    if not path.is_file():
        raise RuntimeError("El vídeo subido ya no existe. Vuelve a elegirlo.")
    if probe_duration(path) <= 0:
        raise RuntimeError("No pude leer ese vídeo. Prueba con MP4 (H.264) o WEBM.")
    out = work / f"source{path.suffix.lower()}"
    shutil.copy2(path, out)
    return out


def _transcribe(cfg: dict, video: Path, work: Path, subtitles: bool = True) -> list[dict]:
    audio = work / "audio.wav"
    ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(audio)])
    # Auto-detect language (not hardcoded to Spanish)
    model = captions_mod._model(cfg)
    segments, _ = model.transcribe(
        str(audio), word_timestamps=subtitles, vad_filter=False, beam_size=1,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    for segment in segments:
        if subtitles:
            for word in segment.words or []:
                token = str(word.word or "").strip()
                if token:
                    words.append({"word": token, "start": float(word.start or 0.0),
                                  "end": float(word.end or 0.0)})
        else:
            # Approximate words from segment text with evenly spaced timestamps
            toks = str(segment.text or "").split()
            if not toks:
                continue
            seg_dur = float(segment.end or 0) - float(segment.start or 0)
            step = seg_dur / len(toks) if toks else 0
            for i, tok in enumerate(toks):
                words.append({"word": tok,
                              "start": float(segment.start or 0) + i * step,
                              "end": float(segment.start or 0) + (i + 1) * step})
    return words


def _detect_moments(cfg: dict, words: list[dict], count: int = 1,
                    target_sec: int = 60) -> list[dict]:
    """Usa el LLM para detectar los mejores momentos (inicio, fin, título)."""
    from . import script_gen

    # Construir transcripción con timestamps
    lines = []
    for w in words:
        lines.append(f"[{w['start']:.1f}-{w['end']:.1f}] {w['word']}")
    transcript = " ".join(f"{w['word']}" for w in words)
    # Limitar tamaño
    transcript = transcript[:6000]

    prompt = (
        "Analiza esta transcripción del vídeo y selecciona el MEJOR momento "
        f"para un clip de aproximadamente {target_sec} segundos. "
        "Elige el fragmento más interesante, impactante, divertido o emotivo. "
        "Busca frases gancho, sorpresas, consejos o momentos de alta retención. "
        "Devuelve SOLO un JSON array con UN objeto: "
        '{"start": segundos_inicio, "end": segundos_fin, "title": "título corto", '
        '"reason": "por qué es viral"}. '
        "Los timestamps deben estar dentro de la duración del vídeo. "
        "No inventes texto. Transcripción:\n" + transcript
    )

    raw = None
    for name in script_gen.provider_order(cfg, include_fallback=False):
        if not script_gen.provider_ready(name):
            continue
        try:
            if name == "gemini":
                raw = script_gen._gemini(cfg, prompt)
            elif name == "openai":
                raw = script_gen._openai(cfg, prompt)
            elif name == "github":
                raw = script_gen._github(cfg, prompt)
            if raw:
                break
        except Exception as exc:
            warn(f"{name}: {str(exc)[:80]}")

    if not raw:
        # Fallback: dividir en partes iguales
        if not words:
            return []
        total = words[-1]["end"]
        segs = []
        for i in range(count):
            s = min(i * target_sec, max(0, total - target_sec))
            e = min(total, s + target_sec)
            segs.append({"start": s, "end": e, "title": f"Clip {i+1}",
                         "reason": "segmento automatico"})
        return segs

    # Extraer JSON
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        moments = json.loads(match.group(0))
    except Exception:
        return []
    # Validar
    out = []
    for m in moments:
        try:
            s = float(m.get("start", 0))
            e = float(m.get("end", s + 30))
            if e - s < 10 or e - s > target_sec * 1.5:
                e = s + target_sec
            out.append({"start": s, "end": e,
                        "title": str(m.get("title", "Clip"))[:60],
                        "reason": str(m.get("reason", ""))[:120]})
        except Exception:
            continue
    return out[:count]


def _cut_clip(video: Path, start: float, end: float, out: Path) -> Path:
    dur = end - start
    width, height, fps = 1080, 1920, 30
    vf = (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
          f"crop={width}:{height},setsar=1,fps={fps}")
    ffmpeg(["-ss", f"{start:.2f}", "-i", str(video), "-t", f"{dur:.2f}",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-r", str(fps), "-c:a", "aac", "-b:a", "128k", str(out)])
    return out


def _subs_for_clip(words: list[dict], start: float, end: float, out: Path) -> Path | None:
    # Filtrar palabras del rango y ajustar timestamps
    clip_words = []
    for w in words:
        if w["end"] < start or w["start"] > end:
            continue
        clip_words.append({
            "word": w["word"],
            "start": max(0.0, w["start"] - start),
            "end": max(0.0, w["end"] - start),
        })
    if not clip_words:
        return None
    # Escribir ASS usando captions_mod helpers
    try:
        cfg = load_config()
        max_words = int(get(cfg, "captions.max_words", 3))
    except Exception:
        max_words = 3
    groups = captions_mod._group(clip_words, max_words)
    lines = []
    lines.append("[Script Info]")
    lines.append("ScriptType: v4.00+")
    lines.append("[V4+ Styles]")
    lines.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    lines.append(f"Style: Default,Arial Black,56,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,4,0,2,40,40,200,1")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    for group in groups:
        if not group:
            continue
        s = group[0]["start"]
        e = group[-1]["end"]
        text = " ".join(w["word"] for w in group)
        lines.append(f"Dialogue: 0,{captions_mod._ass_time(s)},{captions_mod._ass_time(e)},Default,,0,0,0,,{text}")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def generate(url: str, count: int = 1, target_sec: int = 60,
             subtitles: bool = True, source_video: Path | None = None) -> list[dict]:
    """Genera clips desde YouTube o desde un vídeo subido. Devuelve resultados."""
    global _STATE
    ensure_dirs()
    _STATE["progress"] = "Preparando vídeo subido..." if source_video else "Descargando video..."
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    import time
    tag = str(int(time.time() * 1000))
    work = CLIPS_DIR / tag
    work.mkdir(parents=True, exist_ok=True)

    results = []
    try:
        video = use_local_video(source_video, work) if source_video else download(url, work)
        _STATE["progress"] = "Transcribiendo audio..."
        cfg = load_config()
        info("Transcribiendo audio…")
        words = _transcribe(cfg, video, work, subtitles)
        _STATE["progress"] = "Detectando mejores momentos..."
        if not words:
            raise RuntimeError("No se pudo transcribir el audio.")

        info("Detectando mejores momentos…")
        moments = _detect_moments(cfg, words, count, target_sec)
        _STATE["progress"] = f"Generando {len(moments)} clips..."
        if not moments:
            raise RuntimeError("No se detectaron momentos.")

        for i, m in enumerate(moments, 1):
            clip_path = work / f"clip_{i}.mp4"
            sub_path = work / f"clip_{i}.ass"
            try:
                _STATE["progress"] = f"Clip {i}/{len(moments)}: {m['title']}"
                _cut_clip(video, m["start"], m["end"], clip_path)
                final = work / f"clip_{i}_final.mp4"
                if subtitles:
                    _subs_for_clip(words, m["start"], m["end"], sub_path)
                    if sub_path.exists():
                        ffmpeg(["-i", str(clip_path), "-vf",
                                f"ass={sub_path.name}", "-r", "30",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                                "-c:a", "copy", str(final)],
                               cwd=work)
                        if final.exists():
                            clip_path = final
                if not subtitles or not final.exists():
                    # No subtitles: just copy/rename
                    if clip_path != final and not final.exists():
                        import shutil as _sh
                        _sh.copyfile(clip_path, final)
                        clip_path = final if final.exists() else clip_path
                # Guardar en la cola de revisión.
                try:
                    cover = work / f"clip_{i}_cover.jpg"
                    ffmpeg(["-ss", "0.5", "-i", str(clip_path), "-frames:v", "1",
                            "-q:v", "3", str(cover)], cwd=work)
                    script = SimpleNamespace(
                        title=m["title"],
                        description=m["reason"],
                        hashtags=["#shorts", "#clips"],
                        hook=m["title"],
                        full_text="",
                        topic_id="youtube-clip",
                    )
                    folder = queue_mod.save_short(script, clip_path, cover, m["end"] - m["start"])
                    info(f"Clip {i} guardado en cola: {folder.name}")
                except Exception as exc:
                    warn(f"No se pudo guardar en cola: {str(exc)[:80]}")
                results.append({
                    "index": i,
                    "title": m["title"],
                    "reason": m["reason"],
                    "start": m["start"],
                    "end": m["end"],
                    "duration": round(m["end"] - m["start"], 1),
                    "path": str(clip_path),
                    "created_at": stamp(),
                })
                info(f"Clip {i} listo: {m['title']}")
            except Exception as exc:
                warn(f"Clip {i} falló: {str(exc)[:100]}")
                results.append({"index": i, "title": m["title"], "error": str(exc)})
    except Exception as exc:
        warn(f"Error generando clips: {str(exc)[:200]}")
        results.append({"error": str(exc)})
    return results

# Estado global para la UI
_STATE = {"running": False, "progress": "", "results": []}


def get_state() -> dict:
    return {"running": _STATE["running"], "progress": _STATE["progress"],
            "results": _STATE["results"]}


def generate_async(url: str, count: int = 1, target_sec: int = 60,
                   subtitles: bool = True, source_video: Path | None = None) -> None:
    import threading

    def worker():
        _STATE["running"] = True
        _STATE["progress"] = "Iniciando..."
        _STATE["results"] = []
        try:
            from . import util as _u
            orig_info = _u.info
            def info_progress(msg):
                _STATE["progress"] = str(msg)
                orig_info(msg)
            _u.info = info_progress
            res = generate(url, count, target_sec, subtitles, source_video)
            _STATE["results"] = res
            _STATE["progress"] = "Completado"
        except Exception as exc:
            _STATE["results"] = [{"error": str(exc)}]
            _STATE["progress"] = "Error: " + str(exc)[:120]
        finally:
            _STATE["running"] = False
            try:
                _u.info = orig_info
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()
