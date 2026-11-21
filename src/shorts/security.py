"""Seguridad y calidad: reintentos, duplicados, costos y validación."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .util import STATE, read_json, warn, write_json

DUPLICATES_FILE = STATE / "seen_hashes.json"
COSTS_FILE = STATE / "costs.json"


def retry(fn, attempts: int = 3, base_delay: float = 2.0):
    """Ejecuta fn() con reintentos y espera progresiva."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if attempt < attempts:
                delay = base_delay * (2 ** (attempt - 1))
                warn(f"Intento {attempt} falló ({str(exc)[:80]}). Reintentando en {delay:.0f}s…")
                time.sleep(delay)
    if last is not None:
        raise last
    raise RuntimeError("Sin resultado.")


def content_hash(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest()[:16]


def is_duplicate(title: str, text: str) -> bool:
    seen = set(read_json(DUPLICATES_FILE, []) or [])
    return content_hash(title, text) in seen


def mark_seen(title: str, text: str) -> None:
    seen = list(read_json(DUPLICATES_FILE, []) or [])
    digest = content_hash(title, text)
    if digest not in seen:
        seen.append(digest)
    seen = seen[-5000:]
    write_json(DUPLICATES_FILE, seen)


def record_cost(kind: str, amount_usd: float = 0.0, meta: dict | None = None) -> None:
    ledger = read_json(COSTS_FILE, []) or []
    ledger.append({
        "at": time.time(),
        "kind": kind,
        "amount_usd": round(float(amount_usd), 4),
        "meta": meta or {},
    })
    ledger = ledger[-5000:]
    write_json(COSTS_FILE, ledger)


def total_cost(days: int = 30) -> float:
    cutoff = time.time() - days * 86400
    ledger = read_json(COSTS_FILE, []) or []
    return round(sum(float(item.get("amount_usd") or 0)
                    for item in ledger if float(item.get("at") or 0) >= cutoff), 4)


def validate_media(path: Path) -> dict:
    """Comprobación mínima de que el archivo de vídeo es reproducible."""
    from .util import probe_duration
    path = Path(path)
    if not path.exists():
        return {"ok": False, "error": "El archivo no existe."}
    if path.stat().st_size < 100_000:
        return {"ok": False, "error": "El archivo es demasiado pequeño."}
    duration = probe_duration(path)
    if not duration or duration < 1:
        return {"ok": False, "error": "No se pudo leer la duración."}
    return {"ok": True, "duration": round(duration, 2), "size_mb": round(path.stat().st_size / 1_048_576, 2)}