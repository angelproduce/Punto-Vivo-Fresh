"""Arranque del servidor: python -m punto_vivo [--open]

En local usa el puerto 8790. En Render se respeta la variable PORT que asigna
la plataforma (habitualmente 10000) y se activan los headers de proxy para que
las cookies se marquen como Secure bajo HTTPS.
"""
from __future__ import annotations

import argparse
import os
import webbrowser

import uvicorn

from .app import app
from .config import TRUST_PROXY


def main() -> int:
    parser = argparse.ArgumentParser(description="PUNTO VIVO FRESH")
    default_port = os.getenv("PV_PORT") or os.getenv("PORT") or "8790"
    parser.add_argument("--port", type=int, default=int(default_port))
    parser.add_argument("--host", default=os.getenv("PV_HOST", "0.0.0.0"))
    parser.add_argument("--open", action="store_true", help="Abrir el navegador al iniciar")
    args = parser.parse_args()
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}/bienvenida")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=False,
        proxy_headers=TRUST_PROXY,
        forwarded_allow_ips="*" if TRUST_PROXY else "127.0.0.1",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
