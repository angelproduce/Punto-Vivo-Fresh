"""Almacenamiento de fotos de producto y comprobantes privados.

Local: ``static/uploads/`` para fotos (público, como siempre) y
``state/comprobantes/`` para comprobantes (nunca se sirve desde /static).
Producción: Supabase Storage, bucket público ``menu-images`` para fotos y
bucket privado ``comprobantes`` para los comprobantes.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

from .config import (
    PROOF_DIR,
    SUPABASE_BUCKET,
    SUPABASE_URL,
    UPLOAD_DIR,
    supabase_enabled,
)
from .repository import supabase_request

PROOF_BUCKET = "comprobantes"


def optimize_image(data: bytes) -> bytes:
    """Convierte a WebP 1200x1200, igual que hacía app.py antes."""
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=82, method=5)
    return buffer.getvalue()


def save_product_image(product_id: int, data: bytes, filename: str) -> str:
    """Guarda la imagen optimizada y devuelve la URL pública a guardar en BD."""
    webp = optimize_image(data)
    if supabase_enabled():
        path = f"{SUPABASE_BUCKET}/{filename}"
        supabase_request(
            "POST",
            f"/storage/v1/object/{path}",
            content=webp,
            headers={"Content-Type": "image/webp", "x-upsert": "true"},
        )
        return f"{SUPABASE_URL}/storage/v1/object/public/{path}"

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    (Path(UPLOAD_DIR) / filename).write_bytes(webp)
    return "/static/uploads/" + filename


def save_private_file(filename: str, data: bytes, content_type: str) -> str:
    """Guarda un comprobante y devuelve su ruta privada (nunca una URL pública)."""
    if supabase_enabled():
        path = f"{PROOF_BUCKET}/{filename}"
        supabase_request(
            "POST", f"/storage/v1/object/{path}", content=data,
            headers={"Content-Type": content_type, "x-upsert": "false"},
        )
        return path
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    destino = PROOF_DIR / filename
    destino.write_bytes(data)
    return str(destino)


def read_private_file(path: str) -> bytes:
    """Solo el servidor puede leer el comprobante; la ruta la elige la BD."""
    if supabase_enabled():
        return supabase_request("GET", f"/storage/v1/object/{path}").content
    return Path(path).read_bytes()


def delete_private_file(path: str) -> None:
    if supabase_enabled():
        supabase_request("DELETE", f"/storage/v1/object/{path}")
    else:
        destino = Path(path)
        if destino.exists():
            destino.unlink()


def signed_proof_url(path: str, expires_in: int = 300) -> str:
    """URL temporal para el administrador; vacía en local (se sirve por API)."""
    if not supabase_enabled():
        return ''
    respuesta = supabase_request(
        "POST", f"/storage/v1/object/sign/{path}", json={"expiresIn": expires_in}
    ).json()
    url = respuesta.get("signedURL") or respuesta.get("signedUrl") or ''
    return (SUPABASE_URL + url) if url.startswith('/') else url
