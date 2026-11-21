"""Piloto automático: genera y (opcionalmente) publica Shorts sin intervención.

Guarda su configuración en `.state/autopilot.json` y un registro de ejecuciones
en `.state/autopilot_runs.json`. No lanza hilos propios: el bucle lo maneja
el servidor de la UI o un comando periódico (`run.cmd autopilot`).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import pipeline, queue as queue_mod
from .config import get, load_config
from .util import STATE, info, read_json, warn, write_json

CONFIG_FILE = STATE / "autopilot.json"
RUNS_FILE = STATE / "autopilot_runs.json"

DEFAULTS = {
    "enabled": False,
    "daily_count": 1,
    "duration": 90,
    "niche": "",
    "language": "es",
    "voice": "auto",
    "style": "hyperframes",
    "platforms": ["youtube"],
    "approval_mode": "manual",   # manual | auto
    "schedule_hours": [10, 16, 20],
    "budget_usd": 0.0,           # 0 = sin límite
    "max_per_day": 6,
    "topic_id": None,
}


def load() -> dict:
    data = read_json(CONFIG_FILE, {}) or {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def save(cfg: dict) -> dict:
    current = load()
    current.update(cfg or {})
    write_json(CONFIG_FILE, current)
    return current


def runs() -> list[dict]:
    data = read_json(RUNS_FILE, []) or []
    return [item for item in data if isinstance(item, dict)]


def _record(run: dict) -> None:
    history = runs()
    history.append(run)
    history = history[-200:]
    write_json(RUNS_FILE, history)


def is_due(now: float | None = None) -> bool:
    cfg = load()
    if not cfg.get("enabled"):
        return False
    now = now or time.time()
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    today_runs = [r for r in runs() if r.get("date") == today]
    if len(today_runs) >= int(cfg.get("max_per_day") or 1):
        return False
    hour = time.localtime(now).tm_hour
    scheduled = [int(h) for h in (cfg.get("schedule_hours") or [])]
    # Se considera "debido" si la hora actual está en la lista y no ha
    # corrido ya en esa franja.
    if scheduled and hour not in scheduled:
        return False
    slot = f"{today}-{hour}"
    if any(r.get("slot") == slot for r in today_runs):
        return False
    return True


def run_once(force: bool = False) -> dict:
    cfg = load()
    if not cfg.get("enabled") and not force:
        return {"ok": False, "reason": "Piloto desactivado."}
    app_cfg = load_config()
    # Aplica la configuración del piloto sobre la del proyecto.
    if cfg.get("duration"):
        app_cfg.setdefault("project", {})["target_duration"] = int(cfg["duration"])
    # En modo automático se mezclan fondos locales + internet.
    app_cfg.setdefault("media", {})["source"] = "mixed"
    if cfg.get("niche"):
        app_cfg.setdefault("project", {})["niche"] = str(cfg["niche"])
    started = time.time()
    slot = f"{time.strftime('%Y-%m-%d')}-{time.localtime().tm_hour}"
    try:
        folders = pipeline.generate_many(
            app_cfg,
            count=int(cfg.get("daily_count") or 1),
            topic_id=cfg.get("topic_id"),
            seconds=float(cfg.get("duration") or 90),
        )
        ids = [p.name for p in folders]
        if cfg.get("approval_mode") == "auto":
            for short_id in ids:
                try:
                    queue_mod.approve_short(short_id)
                except Exception as exc:
                    warn(f"No pude auto-aprobar {short_id}: {exc}")
        run = {
            "ok": True,
            "date": time.strftime("%Y-%m-%d"),
            "slot": slot,
            "started": started,
            "finished": time.time(),
            "shorts": ids,
            "approval_mode": cfg.get("approval_mode"),
        }
        _record(run)
        info(f"Piloto: {len(ids)} short(s) generados.")
        return run
    except Exception as exc:
        run = {
            "ok": False,
            "date": time.strftime("%Y-%m-%d"),
            "slot": slot,
            "started": started,
            "finished": time.time(),
            "error": str(exc)[:300],
        }
        _record(run)
        warn(f"Piloto falló: {exc}")
        return run