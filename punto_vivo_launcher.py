"""Lanzador de PUNTO VIVO FRESH (el que se convierte en PUNTO VIVO FRESH.exe).

Hace tres cosas y se aparta:
  1. Comprueba que el proyecto portable existe.
  2. Arranca el servidor local en el puerto fijo 8790 si no estaba ya encendido.
  3. Abre el navegador en /bienvenida.

El puerto es fijo a propósito: el QR debe apuntar siempre a la misma dirección.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8790
BASE_URL = f"http://127.0.0.1:{PORT}"
WELCOME_URL = BASE_URL + "/bienvenida"


def messagebox(text: str, title: str = "PUNTO VIVO FRESH", error: bool = True) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    except Exception:  # pragma: no cover - fuera de Windows
        print(f"{title}: {text}")


def project_root() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
    candidates.append(Path(__file__).resolve().parent)
    candidates.append(Path.cwd())
    for folder in candidates:
        if (folder / "punto_vivo" / "app.py").exists():
            return folder
    return None


def ping(timeout: float = 0.8) -> bool:
    """True solo si en el puerto responde esta app (no otro servicio)."""
    try:
        with urllib.request.urlopen(BASE_URL + "/healthz", timeout=timeout) as res:
            data = json.loads(res.read().decode("utf-8", "replace"))
            return data.get("app") == "PUNTO VIVO FRESH"
    except Exception:
        return False


def main() -> int:
    root = project_root()
    if root is None:
        messagebox(
            "No encuentro el proyecto.\n\n"
            "Deja PUNTO VIVO FRESH.exe dentro de la carpeta del proyecto "
            "(donde está la carpeta punto_vivo)."
        )
        return 1

    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        messagebox(
            "Falta el entorno de Python.\n\n"
            "Ejecuta setup.cmd una sola vez (tarda unos minutos) y vuelve a "
            "abrir PUNTO VIVO FRESH.exe."
        )
        return 1

    if ping():
        webbrowser.open(WELCOME_URL)
        return 0

    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handle = (logs / "punto_vivo.log").open("a", encoding="utf-8")
    handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] arrancando en {BASE_URL}\n")
    handle.flush()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + str(root)

    flags = 0
    if os.name == "nt":
        flags = (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    try:
        proc = subprocess.Popen(
            [str(python), "-m", "punto_vivo", "--port", str(PORT)],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except Exception as exc:
        messagebox(f"No pude arrancar el servidor:\n{exc}")
        return 1

    for _ in range(140):
        if ping(0.4):
            webbrowser.open(WELCOME_URL)
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    messagebox(
        f"El servidor no respondió en el puerto {PORT}.\n\n"
        "Comprueba que ese puerto no esté ocupado por otra app y mira "
        "logs\\punto_vivo.log para ver el error."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
