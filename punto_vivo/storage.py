"""Almacenamiento de fotos de producto.

Local: ``punto_vivo/static/uploads/`` (comportamiento original intacto).
Producción: Supabase Storage (bucket público ``menu-images``), porque el
filesystem de Render es efímero y las fotos desaparecerían al reiniciar.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps

from .config import SUPABASE_BUCKET, SUPABASE_URL, UPLOAD_DIR, supabase_enabled
from .repository import supabase_request


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
