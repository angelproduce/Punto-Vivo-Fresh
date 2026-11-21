"""Carga de configuración (config.yaml + .env)."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .util import ROOT

DEFAULTS: dict[str, Any] = {
    "project": {
        "niche": "GTA 6 y tecnología",
        "language": "es",
        "target_duration": 90,
        "min_duration": 60,
        "max_duration": 120,
        "brand": "",
    },
    "script": {
        "provider": "auto",
        "provider_order": ["gemini", "openai", "github"],
        "topics_file": "topics.yaml",
        "research": True,
        "research_sources": ["news", "hn", "github", "wikipedia"],
        "research_per_source": 4,
        "research_timeout": 20,
        "gemini_model": "gemini-3.8-flash",
        "timeout": 240,
        "openai_model": "gpt-4o-mini",
        "github_model": "openai/gpt-4o-mini",
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "llama3.1:8b",
    },
    "voice": {
        "provider": "local",
        "space_id": "Qwen/Qwen3-TTS-Demo",
        "space_voice": "Bodega / 西班牙语-博德加",
        "space_language": "Spanish / 西班牙语",
        "edge_voice": "es-MX-DaliaNeural",
        "edge_rate": "+8%",
        "edge_pitch": "+0Hz",
        "edge_volume": "+0%",
        "edge_chunk_chars": 1200,
        "elevenlabs_voice_id": "",
        "elevenlabs_model": "eleven_multilingual_v2",
        "elevenlabs_chunk_chars": 2000,
        "model_local": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "ref_audio": "assets/voice/reference.wav",
        "ref_text": "assets/voice/reference.txt",
        "x_vector_only": True,
        "speed": 1.18,
        "pause_ms": 150,
        "tone": "breaking",
        "chunk_chars": 240,
        "local_fallback": True,
    },
    "captions": {
        "enabled": True,
        "model": "base",
        "max_words": 3,
        "font": "Arial Black",
        "size": 82,
        "outline": 6,
        "margin_v": 430,
    },
    "media": {
        "source": "auto",
        "query": "gta 6 neon city night",
        "max_clips": 12,
        "youtube_max": 3,
    },
    "graphics": {
        "provider": "none",
        "quality": "looks",
        "seconds": 10,
    },
    "video": {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "crf": 20,
        "preset": "veryfast",
        "hook_seconds": 2.4,
        "voice_lufs": -13.0,
        "voice_tp": -1.0,
        "music": "",
        "music_db": 3.0,
        "music_intro_db": 1.5,
        "music_intro_fade": 0.7,
        "music_fade_in": 0.3,
        "frame_template": "",
        "sfx_enabled": True,
        "sfx_count": 3,
        "sfx_db": -12,
    },
    "publish": {
        "platforms": ["youtube", "tiktok"],
        "auto_publish": False,
        "youtube": {
            "token": "state/youtube_token.json",
            "client_secret": "state/client_secret.json",
        },
        "tiktok": {"token": "state/tiktok_token.json"},
    },
}


def _merge(base: dict, extra: dict) -> dict:
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = ROOT / "config.yaml"
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                _merge(cfg, data)
        except Exception as exc:  # config rota: no bloquees la app
            print(f"  ! config.yaml tiene un error: {exc}")
    env_file = ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=False)
        except Exception:
            pass
    return cfg


def get(cfg: dict, dotted: str, default: Any = None) -> Any:
    current: Any = cfg
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def path_of(value: str | Path) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (ROOT / path)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()
