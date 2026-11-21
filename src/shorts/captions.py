"""Subtítulos automáticos con Whisper + karaoke ASS."""
from __future__ import annotations

import re
from pathlib import Path

from .config import get
from .util import font_file, info, probe_duration, quiet_imports, warn

_WHISPER: dict[str, object] = {}


def _model(cfg: dict):
    name = str(get(cfg, "captions.model", "small"))
    if name not in _WHISPER:
        with quiet_imports():
            from faster_whisper import WhisperModel

        info(f"Cargando Whisper «{name}» (se descarga la primera vez)…")
        _WHISPER[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _WHISPER[name]


def transcribe(cfg: dict, audio: Path) -> list[dict]:
    model = _model(cfg)
    segments, _ = model.transcribe(
        str(audio),
        language="es",
        word_timestamps=True,
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
    )
    words: list[dict] = []
    for segment in segments:
        for word in segment.words or []:
            token = str(word.word or "").strip()
            if token:
                words.append({
                    "word": token,
                    "start": float(word.start or 0.0),
                    "end": float(word.end or 0.0),
                })
    return words


def _fallback(text: str, duration: float, offset: float = 0.0) -> list[dict]:
    tokens = re.findall(r"[\wáéíóúüñÁÉÍÓÚÜÑ]+|[.,!?]", text or "")
    tokens = [t for t in tokens if re.search(r"\w", t)]
    span = max(0.0, float(duration) - float(offset))
    if not tokens or span <= 0:
        return []
    step = span / len(tokens)
    return [{"word": t, "start": offset + i * step, "end": offset + (i + 1) * step}
            for i, t in enumerate(tokens)]


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _group(words: list[dict], max_words: int) -> list[list[dict]]:
    lines: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        current.append(word)
        token = word["word"]
        if len(current) >= max_words or token.endswith((".", "?", "!", "…", ",")):
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    return lines


def build(cfg: dict, audio: Path, out: Path, text: str = "",
          words: list[dict] | None = None, offset: float = 0.0) -> Path | None:
    if not bool(get(cfg, "captions.enabled", True)):
        return None

    duration = probe_duration(audio)
    if words is None:
        try:
            words = transcribe(cfg, audio)
        except Exception as exc:
            warn(f"Whisper falló ({exc}); uso tiempos aproximados.")
            words = []
    if not words and text:
        words = _fallback(text, duration, offset)
    if not words:
        raise RuntimeError("No pude generar subtítulos para este audio.")

    max_words = int(get(cfg, "captions.max_words", 3) or 3)
    lines = _group(words, max_words)

    font = str(get(cfg, "captions.font", "Arial Black"))
    size = int(get(cfg, "captions.size", 82) or 82)
    outline = int(get(cfg, "captions.outline", 6) or 6)
    margin = int(get(cfg, "captions.margin_v", 430) or 430)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,{font},{size},&H0057E0FF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,80,80,{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    for index, line in enumerate(lines):
        start = max(0.0, line[0]["start"] - 0.06)
        end = max(start + 0.35, line[-1]["end"] + 0.10)
        if index + 1 < len(lines):
            end = min(end, max(start + 0.35, lines[index + 1][0]["start"] - 0.02))
        if end <= start:
            end = start + 0.35

        parts: list[str] = []
        previous = start
        for word in line:
            word_start = max(previous, word["start"])
            lead = max(0.0, word_start - previous)
            hold = max(0.12, word["end"] - word_start)
            if lead > 0.02:
                parts.append(f"{{\\k{int(round(lead * 100))}}}")
            token = re.sub(r"[{}]", "", word["word"]).replace("\\", "").upper()
            parts.append(f"{{\\k{int(round(hold * 100))}}}{token} ")
            previous = word_start + hold
        tail = min(0.30, max(0.0, end - previous))
        if tail > 0.02:
            parts.append(f"{{\\k{int(round(tail * 100))}}}")

        body = "".join(parts).strip().replace("}{", "}{")
        events.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Shorts,,0,0,0,,"
            f"{{\\fad(70,70)\\blur0.4}}{body}"
        )

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return out


def srt(cfg: dict, audio: Path, out: Path, text: str = "") -> Path | None:
    """SRT sencillo (por si algún día hace falta para publicar)."""
    if not bool(get(cfg, "captions.enabled", True)):
        return None
    duration = probe_duration(audio)
    try:
        words = transcribe(cfg, audio)
    except Exception:
        words = _fallback(text, duration)
    if not words:
        return None
    lines = _group(words, int(get(cfg, "captions.max_words", 3) or 3))
    blocks = []
    for index, line in enumerate(lines, 1):
        start = max(0.0, line[0]["start"] - 0.05)
        end = max(start + 0.4, line[-1]["end"] + 0.1)
        body = " ".join(w["word"] for w in line).upper()
        blocks.append(
            f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{body}\n"
        )
    out = Path(out)
    out.write_text("\n".join(blocks), encoding="utf-8")
    return out


def _srt_time(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
