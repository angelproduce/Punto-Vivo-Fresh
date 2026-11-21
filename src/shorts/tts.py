"""Voz para los shorts.

Dos motores, ambos gratis:

  - edge   voz neuronal en la nube de Microsoft (rápida: ~10 s por minuto de
           audio, sin clave ni cuenta). No es tu voz clonada.
  - local  Qwen3-TTS en tu PC con tu voz clonada (muy lenta: ~17-23 s de
           proceso por cada segundo de audio, o sea ~30 min por un short de 90 s).

`voice.provider: auto` usa la voz rápida y, si no hay internet, cae a la clonada.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Callable

from .config import get, path_of
from .util import (WORK, ffmpeg, info, probe_duration, quiet_imports, warn)

Progress = Callable[[int, int, str], None]

_MODEL = None
_MODEL_LOCK = threading.Lock()


# ------------------------------------------------------------------ referencia
def prepare_reference(source: Path, target: Path, max_seconds: float = 25.0) -> Path:
    """Convierte cualquier audio en una referencia limpia: 24 kHz, mono, sin silencios."""
    source, target = Path(source), Path(target)
    if not source.exists():
        raise FileNotFoundError("No encuentro el audio.")
    if source.stat().st_size < 2048:
        raise ValueError("El archivo está vacío.")
    total = probe_duration(source)
    if total and total < 3:
        raise ValueError("El audio es demasiado corto (mínimo 3 segundos).")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + "-tmp.wav")
    trimmed = target.with_name(target.stem + "-trim.wav")
    # FFmpeg 7.1 falla al encadenar silenceremove + loudnorm, así que van en dos pasos.
    ffmpeg([
        "-i", str(source), "-vn", "-ac", "1", "-ar", "24000",
        "-t", f"{max_seconds:.2f}",
        "-af", "silenceremove=start_periods=1:start_silence=0.15:start_threshold=-45dB",
        "-c:a", "pcm_s16le", str(trimmed),
    ])
    ffmpeg([
        "-i", str(trimmed), "-ac", "1", "-ar", "24000",
        "-af", "loudnorm=I=-18:TP=-6:LRA=11",
        "-c:a", "pcm_s16le", str(tmp),
    ])
    trimmed.unlink(missing_ok=True)
    duration = probe_duration(tmp)
    if duration < 3:
        tmp.unlink(missing_ok=True)
        raise ValueError("Casi no hay voz en ese audio. Prueba otra grabación.")
    tmp.replace(target)
    return target


# ------------------------------------------------------------------ local
def _load_model(cfg: dict):
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        with quiet_imports():
            import torch
            from qwen_tts import Qwen3TTSModel

        threads = int(get(cfg, "voice.threads", 0) or 0)
        if threads <= 0:
            threads = max(1, (os.cpu_count() or 4))
        torch.set_num_threads(threads)

        model_id = str(get(cfg, "voice.model_local", "Qwen/Qwen3-TTS-12Hz-0.6B-Base"))
        info(f"Cargando la voz clonada ({model_id})… la primera vez tarda un poco.")
        _MODEL = Qwen3TTSModel.from_pretrained(
            model_id,
            device_map="cpu",
            dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        return _MODEL


def _split(text: str, limit: int) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            parts = re.split(r"(?<=[,;:])\s+", sentence)
        else:
            parts = [sentence]
        for part in parts:
            if not current:
                current = part
            elif len(current) + 1 + len(part) <= limit:
                current = f"{current} {part}"
            else:
                chunks.append(current)
                current = part
    if current:
        chunks.append(current)

    final: list[str] = []
    for chunk in chunks:
        while len(chunk) > limit * 1.6:
            cut = chunk.rfind(" ", 0, limit)
            cut = cut if cut > 20 else limit
            final.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            final.append(chunk)
    return final


def _synth_local(cfg: dict, text: str, out: Path, progress: Progress | None) -> Path:
    import numpy as np
    import soundfile as sf

    model = _load_model(cfg)
    ref = path_of(get(cfg, "voice.ref_audio", "assets/voice/reference.wav"))
    if not ref.exists():
        raise FileNotFoundError("Falta tu voz de referencia (sube un audio en «Mi voz»).")

    prompt = model.create_voice_clone_prompt(
        str(ref),
        ref_text=None,
        x_vector_only_mode=bool(get(cfg, "voice.x_vector_only", True)),
    )

    chunks = _split(text, int(get(cfg, "voice.chunk_chars", 240) or 240))
    if not chunks:
        raise ValueError("El guion está vacío.")

    pause = int(get(cfg, "voice.pause_ms", 200) or 0) / 1000.0
    pieces: list[np.ndarray] = []
    rate = 24000
    for index, chunk in enumerate(chunks, 1):
        if progress:
            progress(index, len(chunks), chunk)
        wavs, rate = model.generate_voice_clone(
            text=chunk,
            language="Spanish",
            voice_clone_prompt=prompt,
            non_streaming_mode=True,
            do_sample=True,
            temperature=0.7,
            top_p=0.85,
            repetition_penalty=1.08,
        )
        if not wavs or wavs[0] is None or len(wavs[0]) == 0:
            raise RuntimeError("La voz salió vacía. Vuelve a intentarlo.")
        pieces.append(np.asarray(wavs[0], dtype=np.float32).reshape(-1))
        if index < len(chunks) and pause > 0:
            pieces.append(np.zeros(int(rate * pause), dtype=np.float32))

    audio = np.concatenate(pieces)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.99:
        audio = audio * (0.99 / peak)

    raw = out.with_name(out.stem + "-raw.wav")
    raw.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(raw), audio, int(rate))

    speed = float(get(cfg, "voice.speed", 1.0) or 1.0)
    speed = min(2.0, max(0.5, speed))
    filters = ["loudnorm=I=-16:TP=-1.5:LRA=11"]
    if abs(speed - 1.0) > 0.02:
        filters.append(f"atempo={speed:.3f}")
    ffmpeg([
        "-i", str(raw), "-ac", "1", "-ar", "48000",
        "-af", ",".join(filters), "-c:a", "pcm_s16le", str(out),
    ])
    raw.unlink(missing_ok=True)
    return out


# ------------------------------------------------------------------ edge (rápida)
def _synth_edge(cfg: dict, text: str, out: Path, progress: Progress | None = None) -> Path:
    """Voz neuronal gratis de Microsoft Edge. Sin clave y muy rápida."""
    import edge_tts

    voice = str(get(cfg, "voice.edge_voice", "es-MX-DaliaNeural") or "es-MX-DaliaNeural")
    rate = str(get(cfg, "voice.edge_rate", "+8%") or "+8%")
    volume = str(get(cfg, "voice.edge_volume", "+0%") or "+0%")
    pitch = str(get(cfg, "voice.edge_pitch", "+0Hz") or "+0Hz")
    chunks = _split(text, int(get(cfg, "voice.edge_chunk_chars", 1200) or 1200))
    if not chunks:
        raise ValueError("El guion está vacío.")

    temp = out.with_name(out.stem + "-edge")
    temp.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    try:
        for index, chunk in enumerate(chunks, 1):
            if progress:
                progress(index, len(chunks), chunk)
            part = temp / f"{index:03d}.mp3"
            communicate = edge_tts.Communicate(
                chunk, voice, rate=rate, volume=volume, pitch=pitch,
            )
            asyncio.run(communicate.save(str(part)))
            if not part.exists() or part.stat().st_size < 1024:
                raise RuntimeError("La voz rápida no devolvió audio.")
            files.append(part)

        concat = temp / "list.txt"
        concat.write_text(
            "\n".join(f"file '{p.name}'" for p in files), encoding="utf-8"
        )
        final = temp / "voz.wav"
        ffmpeg([
            "-f", "concat", "-safe", "0", "-i", concat.name,
            "-ac", "1", "-ar", "48000",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a", "pcm_s16le", final.name,
        ], cwd=temp)
        final.replace(out)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    if not out.exists():
        raise RuntimeError("No pude montar la voz rápida.")
    return out


# ------------------------------------------------------------------ space
def _synth_space(cfg: dict, text: str, out: Path) -> Path:
    """HF Space de Qwen3-TTS (voces predefinidas, no clonación)."""
    from gradio_client import Client

    space = str(get(cfg, "voice.space_id", "Qwen/Qwen3-TTS-Demo"))
    voice = str(get(cfg, "voice.space_voice", "Bodega / 西班牙语-博德加"))
    language = str(get(cfg, "voice.space_language", "Spanish / 西班牙语"))
    client = Client(space, verbose=False)
    result = client.predict(
        text=text,
        voice_display=voice,
        language_display=language,
        api_name="/tts_interface",
    )
    candidate = result[0] if isinstance(result, (list, tuple)) else result
    path = Path(str(candidate))
    if not path.exists():
        raise RuntimeError("La nube no devolvió un audio válido.")
    ffmpeg(["-i", str(path), "-ac", "1", "-ar", "48000",
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "pcm_s16le", str(out)])
    return out


def _synth_elevenlabs(cfg: dict, text: str, out: Path, progress: Progress | None = None) -> Path:
    import requests
    key = env("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Falta ELEVENLABS_API_KEY")
    voice_id = str(get(cfg, "voice.elevenlabs_voice_id", "") or env("ELEVENLABS_VOICE_ID") or "").strip()
    if not voice_id:
        raise RuntimeError("Falta el ID de voz de ElevenLabs (voice.elevenlabs_voice_id).")
    model = str(get(cfg, "voice.elevenlabs_model", "eleven_multilingual_v2"))
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    chunks = _split(text, int(get(cfg, "voice.elevenlabs_chunk_chars", 2000) or 2000))
    if not chunks:
        raise ValueError("El guion está vacío.")
    temp = out.with_name(out.stem + "-el")
    temp.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    try:
        for index, chunk in enumerate(chunks, 1):
            if progress:
                progress(index, len(chunks), chunk)
            part = temp / f"{index:03d}.mp3"
            response = requests.post(
                url,
                headers={"xi-api-key": key, "Accept": "audio/mpeg", "Content-Type": "application/json"},
                json={"text": chunk, "model_id": model, "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
                timeout=120,
            )
            response.raise_for_status()
            part.write_bytes(response.content)
            files.append(part)
        concat = temp / "list.txt"
        concat.write_text("\n".join(f"file '{p.name}'" for p in files), encoding="utf-8")
        final = temp / "voz.wav"
        ffmpeg(["-f", "concat", "-safe", "0", "-i", concat.name,
                "-ac", "1", "-ar", "48000", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-c:a", "pcm_s16le", final.name], cwd=temp)
        final.replace(out)
    finally:
        import shutil
        shutil.rmtree(temp, ignore_errors=True)
    if not out.exists():
        raise RuntimeError("No pude montar la voz de ElevenLabs.")
    return out


# ------------------------------------------------------------------ API
def synthesize(cfg: dict, text: str, out: Path, progress: Progress | None = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    provider = str(get(cfg, "voice.provider", "auto")).lower()

    if provider == "elevenlabs":
        try:
            info("Generando la voz con ElevenLabs…")
            return _synth_elevenlabs(cfg, text, out, progress)
        except Exception as exc:
            warn(f"ElevenLabs no está disponible ({str(exc)[:120]}).")
            if not bool(get(cfg, "voice.local_fallback", True)):
                raise

    if provider in {"edge", "auto", "space"}:
        try:
            info("Generando la voz rápida (Edge, gratis)…")
            return _synth_edge(cfg, text, out, progress)
        except Exception as exc:
            message = str(exc)
            warn(f"La voz rápida no está disponible ({message[:120]}).")
            if provider == "edge" and not bool(get(cfg, "voice.local_fallback", True)):
                raise

    if provider in {"local", "auto", "space", "edge"}:
        return _synth_local(cfg, text, out, progress)
    raise ValueError(f"Proveedor de voz desconocido: {provider}")
