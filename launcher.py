"""Lanzador de la interfaz (el que se convierte en ANGEL PRODUCE IA.exe).

Hace tres cosas y se aparta:
  1. Comprueba que el entorno portable existe.
  2. Arranca el servidor local si no estaba ya encendido.
  3. Abre el navegador en la interfaz.

El servidor sigue vivo hasta que pulses "Salir" dentro de la app.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

PORT = 8765


def messagebox(text: str, title: str = "ANGEL PRODUCE IA", error: bool = True) -> None:
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
        if (folder / "config.yaml").exists() and (folder / "src" / "shorts").is_dir():
            return folder
    return None


def ping(port: int, timeout: float = 0.8) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8", "replace"))
    except Exception:
        return None


def free_port(start: int = PORT) -> int:
    for port in range(start, start + 25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main() -> int:
    root = project_root()
    if root is None:
        messagebox(
            "No encuentro el proyecto.\n\n"
            "Deja ANGEL PRODUCE IA.exe dentro de la carpeta donde está config.yaml "
            "(la carpeta de ANGEL PRODUCE IA)."
        )
        return 1

    python = root / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        messagebox(
            "Falta el entorno de Python.\n\n"
            "Abre setup.cmd una sola vez (tarda unos minutos) y vuelve a "
            "abrir ANGEL PRODUCE IA.exe."
        )
        return 1

    for port in range(PORT, PORT + 25):
        if ping(port):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return 0

    port = free_port()
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handle = (logs / "ui.log").open("a", encoding="utf-8")
    handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] arrancando UI en {port}\n")
    handle.flush()

    env = os.environ.copy()
    env["HF_HOME"] = str(root / ".cache" / "huggingface")
    env["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + str(root / ".venv" / "Lib" / "site-packages")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")

    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(
            [str(python), "-m", "shorts.ui", "--port", str(port)],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except Exception as exc:
        messagebox(f"No pude arrancar la interfaz:\n{exc}")
        return 1

    for _ in range(140):
        if ping(port, 0.4):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.5)

    messagebox(
        "La interfaz no respondió.\n\n"
        "Mira logs\\ui.log o ejecuta 'run.cmd doctor' para ver qué falta."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
