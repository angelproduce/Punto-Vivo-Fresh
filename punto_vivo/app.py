"""PUNTO VIVO FRESH · aplicación FastAPI (público + panel administrativo)."""
from __future__ import annotations

import csv
import io
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import qrcode
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import admin as admin_tools
from . import repository, security, storage, visits
from .config import (
    ADMIN_COOKIE,
    CSRF_HEADER,
    MENU_PATH,
    TRUST_PROXY,
    VISITOR_COOKIE,
    WELCOME_PATH,
    MAX_IMAGE_BYTES,
    ensure_dirs,
    now_local,
    welcome_url,
)

BASE_DIR = Path(__file__).resolve().parent
ensure_dirs()

app = FastAPI(title="PUNTO VIVO FRESH", docs_url=None, redoc_url=None, openapi_url=None)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
def startup() -> None:
    repository.init_db()
    admin_tools.bootstrap_from_env()


# ------------------------------------------------------------------ helpers

def client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconocido"


def public_host(request: Request) -> str:
    """Host público real. Detrás de Render/Cloudflare viene en X-Forwarded-Host."""
    host = request.headers.get("host", "")
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-host", "")
        if forwarded:
            host = forwarded.split(",")[0].strip()
    return host


def origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return urlparse(origin).netloc.lower() == public_host(request).lower()


def require_admin(request: Request) -> Any:
    session = security.get_session(request.cookies.get(ADMIN_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Sesión expirada. Inicia sesión de nuevo.")
    if not origin_ok(request):
        raise HTTPException(status_code=403, detail="Origen no permitido.")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.headers.get(CSRF_HEADER) != session["csrf_token"]:
            raise HTTPException(status_code=403, detail="Token de seguridad inválido.")
    return session


def require_visitor(request: Request) -> bool:
    return security.verify_visitor(request.cookies.get(VISITOR_COOKIE)) is not None


def public_context(request: Request, **extra: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {"request": request, "brand": "PUNTO VIVO FRESH"}
    ctx.update(extra)
    return ctx


def _csv_safe(value: Any) -> Any:
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def csv_response(filename: str, header: list[str], rows: list[list[Any]]) -> Response:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([_csv_safe(v) for v in header])
    writer.writerows([[_csv_safe(v) for v in row] for row in rows])
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def categoria_activa(categoria_id: int) -> None:
    if repository.active_category(categoria_id) is None:
        raise HTTPException(status_code=400, detail="La categoría no existe o está inactiva.")


# ------------------------------------------------------------------ modelos

class VisitIn(BaseModel):
    full_name: str
    phone: str
    consent: bool
    request_token: str
    website: str = ""  # honeypot: los bots suelen rellenarlo


class LoginIn(BaseModel):
    email: str
    password: str


class CategoryIn(BaseModel):
    name: str
    description: str = ""
    image: str = ""
    sort_order: int = 0
    active: bool = True


class ProductIn(BaseModel):
    category_id: int
    name: str
    description: str = ""
    price: float = 0
    image: str = ""
    available: bool = True
    sort_order: int = 0
    is_demo: bool = False


# ------------------------------------------------------------------ páginas públicas

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(WELCOME_PATH, status_code=302)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "app": "PUNTO VIVO FRESH"}


@app.get(WELCOME_PATH, response_class=HTMLResponse)
def bienvenida(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "bienvenida.html", public_context(request, menu_path=MENU_PATH)
    )


@app.post("/api/visita")
def api_visita(payload: VisitIn, request: Request) -> JSONResponse:
    if payload.website.strip():
        raise HTTPException(status_code=400, detail="No se pudo procesar el formulario.")

    try:
        full_name = security.validate_name(payload.full_name)
        phone = security.normalize_phone(payload.phone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not payload.consent:
        raise HTTPException(status_code=400, detail="Debes aceptar el aviso de privacidad para continuar.")

    try:
        result = visits.register_visit(full_name, phone, payload.request_token, client_ip(request))
    except visits.VisitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cookie = security.sign_visitor(
        {
            "v": result["visit_id"],
            "n": result["visit_number"],
            "exp": time.time() + 12 * 3600,
        }
    )
    response = JSONResponse(
        {
            "ok": True,
            "menu_url": MENU_PATH,
            "visit_number": result["visit_number"],
            "duplicate": result["duplicate"],
            "message": (
                "Registramos tu visita anterior, no se creó una nueva."
                if result["duplicate"]
                else f"¡Bienvenido, visita número {result['visit_number']}!"
            ),
        }
    )
    response.set_cookie(
        VISITOR_COOKIE,
        cookie,
        max_age=12 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.get("/api/visita/actual")
def api_visita_actual(request: Request) -> dict[str, Any]:
    payload = security.verify_visitor(request.cookies.get(VISITOR_COOKIE))
    if payload is None:
        raise HTTPException(status_code=401, detail="Sin visita registrada.")
    return {"visit_number": payload.get("n"), "visit_id": payload.get("v")}


@app.get(MENU_PATH, response_class=HTMLResponse)
def menu(request: Request) -> Response:
    if not require_visitor(request):
        return RedirectResponse(WELCOME_PATH + "?motivo=requerido", status_code=302)
    return templates.TemplateResponse("menu.html", public_context(request))


@app.get("/api/menu")
def api_menu(request: Request) -> dict[str, Any]:
    if not require_visitor(request):
        raise HTTPException(status_code=401, detail="Regístrate escaneando el código QR para ver el menú.")
    categorias = repository.menu_categories()
    productos = repository.menu_products()
    items = []
    for row in productos:
        item = dict(row)
        item["price_label"] = "Precio por definir" if item["is_demo"] and float(item["price"]) == 0 else f"RD$ {float(item['price']):,.2f}"
        items.append(item)
    return {
        "categorias": [dict(c) for c in categorias],
        "productos": items,
        "aviso": "Productos de demostración. Los datos reales y precios serán cargados por el administrador.",
    }


@app.get("/api/producto/{producto_id}")
def api_producto(producto_id: int, request: Request) -> dict[str, Any]:
    if not require_visitor(request):
        raise HTTPException(status_code=401, detail="Acceso requiere registro previo.")
    row = repository.menu_product(producto_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Producto no disponible.")
    item = dict(row)
    item["price_label"] = (
        "Precio por definir" if item["is_demo"] and float(item["price"]) == 0
        else f"RD$ {float(item['price']):,.2f}"
    )
    return item


# ------------------------------------------------------------------ autenticación admin

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request) -> Response:
    if security.get_session(request.cookies.get(ADMIN_COOKIE)):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin_login.html", public_context(request))


@app.post("/api/admin/login")
def api_admin_login(payload: LoginIn, request: Request) -> JSONResponse:
    if not origin_ok(request):
        raise HTTPException(status_code=403, detail="Origen no permitido.")
    client_hash = security.ip_hash(client_ip(request))
    if not security.allow_login(client_hash):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera una hora.")

    row = repository.get_admin_by_email(payload.email.strip().lower())
    if row is None or not security.verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos.")

    token, csrf = security.create_session(row["id"])
    response = JSONResponse({"ok": True, "csrf_token": csrf, "email": row["email"]})
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=8 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.post("/api/admin/logout")
def api_admin_logout(request: Request, session=Depends(require_admin)) -> JSONResponse:
    security.delete_session(request.cookies.get(ADMIN_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


@app.get("/api/admin/me")
def api_admin_me(session=Depends(require_admin)) -> dict[str, Any]:
    return {"email": session["email"], "role": session["role"], "csrf_token": session["csrf_token"]}


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> Response:
    session = security.get_session(request.cookies.get(ADMIN_COOKIE))
    if session is None:
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse(
        "admin.html", public_context(request, admin_email=session["email"], csrf=session["csrf_token"])
    )


# ------------------------------------------------------------------ resumen y consultas

@app.get("/api/admin/resumen")
def api_resumen(session=Depends(require_admin)) -> dict[str, Any]:
    hoy = now_local()
    hoy_s = hoy.strftime("%Y-%m-%d")
    semana_s = (hoy - timedelta(days=6)).strftime("%Y-%m-%d")
    mes_s = hoy.strftime("%Y-%m")

    serie = repository.visit_daily_series(semana_s)
    dias = []
    for offset in range(6, -1, -1):
        fecha = (hoy - timedelta(days=offset)).strftime("%Y-%m-%d")
        dias.append({"fecha": fecha, "visitas": int(serie.get(fecha, 0))})

    return {
        "total_clientes": repository.count_customers(),
        "total_visitas": repository.count_visits(),
        "visitas_hoy": repository.count_visits({"desde": hoy_s, "hasta": hoy_s}),
        "visitas_semana": repository.count_visits({"desde": semana_s}),
        "visitas_mes": repository.count_visits({"desde": mes_s + "-01"}),
        "top_clientes": [
            {
                "id": r["id"],
                "nombre": r["full_name"],
                "telefono": security.mask_phone(r["phone"]),
                "total": r["total"],
                "primera": r["primera"],
                "ultima": r["ultima"],
            }
            for r in repository.top_customers(6)
        ],
        "serie_diaria": dias,
        "productos_activos": repository.count_products_available(),
        "demo_productos": repository.count_demo_products(),
    }


@app.get("/api/admin/clientes")
def api_clientes(q: str = "", session=Depends(require_admin)) -> dict[str, Any]:
    rows = repository.search_customers(q)
    return {
        "clientes": [
            {
                "id": r["id"],
                "nombre": r["full_name"],
                "telefono": r["phone"],
                "total": r["total"],
                "primera": r["primera"] or "—",
                "ultima": r["ultima"] or "—",
            }
            for r in rows
        ]
    }


@app.get("/api/admin/clientes/{cliente_id}/visitas")
def api_historial(cliente_id: int, session=Depends(require_admin)) -> dict[str, Any]:
    cliente = repository.get_customer(cliente_id)
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    rows = repository.customer_visits(cliente_id)
    return {
        "cliente": {"nombre": cliente["full_name"], "telefono": cliente["phone"]},
        "visitas": [
            {
                "numero": r["visit_number"],
                "fecha": r["visit_date"],
                "hora": r["visit_time"],
                "registro": r["created_at"],
            }
            for r in rows
        ],
    }


def filtro_visitas(q: str, desde: str, hasta: str) -> dict[str, str]:
    """Filtros independientes del backend (SQLite o Supabase)."""
    return {"q": q, "desde": desde, "hasta": hasta}


@app.get("/api/admin/visitas")
def api_visitas(q: str = "", desde: str = "", hasta: str = "", limite: int = 100,
                session=Depends(require_admin)) -> dict[str, Any]:
    filters = filtro_visitas(q, desde, hasta)
    total = repository.count_visits(filters)
    rows = repository.list_visits(filters, min(max(limite, 1), 1000))
    return {
        "total": total,
        "visitas": [
            {
                "id": r["id"],
                "numero": r["visit_number"],
                "nombre": r["full_name"],
                "telefono": r["phone"],
                "fecha": r["visit_date"],
                "hora": r["visit_time"],
                "registro": r["created_at"],
            }
            for r in rows
        ],
    }


@app.get("/api/admin/exportar/visitas.csv")
def exportar_visitas(q: str = "", desde: str = "", hasta: str = "",
                     session=Depends(require_admin)) -> Response:
    filters = filtro_visitas(q, desde, hasta)
    rows = repository.list_visits(filters)
    return csv_response(
        "punto_vivo_visitas.csv",
        ["ID visita", "Nombre", "Teléfono", "N.º visita", "Fecha", "Hora", "Registro exacto"],
        [
            [r["id"], r["full_name"], r["phone"], r["visit_number"],
             r["visit_date"], r["visit_time"], r["created_at"]]
            for r in rows
        ],
    )


@app.get("/api/admin/exportar/clientes.csv")
def exportar_clientes(session=Depends(require_admin)) -> Response:
    rows = repository.all_customers_with_totals()
    return csv_response(
        "punto_vivo_clientes.csv",
        ["ID", "Nombre", "Teléfono", "Total visitas", "Primera visita", "Última visita"],
        [[r["id"], r["full_name"], r["phone"], r["total"], r["primera"] or "", r["ultima"] or ""]
         for r in rows],
    )


# ------------------------------------------------------------------ categorías

@app.get("/api/admin/categorias")
def api_categorias(session=Depends(require_admin)) -> dict[str, Any]:
    rows = repository.admin_categories()
    return {"categorias": [dict(r) for r in rows]}


@app.post("/api/admin/categorias")
def crear_categoria(payload: CategoryIn, session=Depends(require_admin)) -> dict[str, Any]:
    name = payload.name.strip()
    if not (2 <= len(name) <= 80):
        raise HTTPException(status_code=400, detail="El nombre debe tener entre 2 y 80 caracteres.")
    if repository.get_category_by_name(name):
        raise HTTPException(status_code=409, detail="Ya existe esa categoría.")
    new_id = repository.create_category(
        name, payload.description.strip(), payload.image.strip(),
        payload.sort_order, payload.active,
    )
    return {"ok": True, "id": new_id}


@app.put("/api/admin/categorias/{categoria_id}")
def editar_categoria(categoria_id: int, payload: CategoryIn,
                     session=Depends(require_admin)) -> dict[str, Any]:
    if repository.get_category(categoria_id) is None:
        raise HTTPException(status_code=404, detail="Categoría no encontrada.")
    name = payload.name.strip()
    if not (2 <= len(name) <= 80):
        raise HTTPException(status_code=400, detail="El nombre debe tener entre 2 y 80 caracteres.")
    repository.update_category(
        categoria_id, name, payload.description.strip(), payload.image.strip(),
        payload.sort_order, payload.active,
    )
    return {"ok": True}


@app.delete("/api/admin/categorias/{categoria_id}")
def eliminar_categoria(categoria_id: int, session=Depends(require_admin)) -> dict[str, Any]:
    if repository.get_category(categoria_id) is None:
        raise HTTPException(status_code=404, detail="Categoría no encontrada.")
    productos = repository.count_products_in_category(categoria_id)
    if productos:
        raise HTTPException(
            status_code=409,
            detail=f"La categoría tiene {productos} producto(s). Muévelos o elimínalos antes.",
        )
    repository.delete_category(categoria_id)
    return {"ok": True}


# ------------------------------------------------------------------ productos

@app.get("/api/admin/productos")
def api_productos(session=Depends(require_admin)) -> dict[str, Any]:
    rows = repository.admin_products()
    return {"productos": [dict(r) for r in rows]}


def validar_producto(payload: ProductIn) -> tuple[str, str, float]:
    name = payload.name.strip()
    if not (2 <= len(name) <= 120):
        raise HTTPException(status_code=400, detail="El nombre debe tener entre 2 y 120 caracteres.")
    categoria_activa(payload.category_id)
    price = round(float(payload.price or 0), 2)
    if price < 0 or price > 99_999_999.99:
        raise HTTPException(status_code=400, detail="Precio inválido.")
    return name, payload.description.strip()[:1000], price


@app.post("/api/admin/productos")
def crear_producto(payload: ProductIn, session=Depends(require_admin)) -> dict[str, Any]:
    name, description, price = validar_producto(payload)
    now = now_local().isoformat(timespec="seconds")
    new_id = repository.create_product({
        "category_id": payload.category_id,
        "name": name,
        "description": description,
        "price": price,
        "image": payload.image.strip()[:500],
        "available": payload.available,
        "is_demo": payload.is_demo,
        "sort_order": payload.sort_order,
        "created_at": now,
        "updated_at": now,
    })
    return {"ok": True, "id": new_id}


@app.put("/api/admin/productos/{producto_id}")
def editar_producto(producto_id: int, payload: ProductIn,
                    session=Depends(require_admin)) -> dict[str, Any]:
    if repository.get_product(producto_id) is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    name, description, price = validar_producto(payload)
    repository.update_product(producto_id, {
        "category_id": payload.category_id,
        "name": name,
        "description": description,
        "price": price,
        "image": payload.image.strip()[:500],
        "available": payload.available,
        "is_demo": payload.is_demo,
        "sort_order": payload.sort_order,
        "updated_at": now_local().isoformat(timespec="seconds"),
    })
    return {"ok": True}


@app.delete("/api/admin/productos/{producto_id}")
def eliminar_producto(producto_id: int, session=Depends(require_admin)) -> dict[str, Any]:
    if repository.get_product(producto_id) is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    repository.delete_product(producto_id)
    return {"ok": True}


@app.patch("/api/admin/productos/{producto_id}/estado")
def cambiar_estado(producto_id: int, payload: dict[str, Any],
                   session=Depends(require_admin)) -> dict[str, Any]:
    if repository.get_product(producto_id) is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    disponible = bool(payload.get("available"))
    repository.set_product_available(
        producto_id, disponible, now_local().isoformat(timespec="seconds")
    )
    return {"ok": True, "available": disponible}


@app.patch("/api/admin/productos/{producto_id}/orden")
def cambiar_orden(producto_id: int, payload: dict[str, Any],
                  session=Depends(require_admin)) -> dict[str, Any]:
    try:
        orden = int(payload.get("sort_order"))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Orden inválido.")
    repository.set_product_sort_order(producto_id, orden)
    return {"ok": True}


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


@app.post("/api/admin/productos/{producto_id}/imagen")
async def subir_imagen(producto_id: int, request: Request, file: UploadFile = File(...),
                       session=Depends(require_admin)) -> dict[str, Any]:
    if repository.get_product(producto_id) is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado.")
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Formato no permitido. Usa JPG, PNG, WebP o GIF.")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"La imagen supera los {MAX_IMAGE_BYTES // (1024 * 1024)} MB.",
        )
    filename = f"producto_{producto_id}_{uuid4().hex[:10]}.webp"
    try:
        url = storage.save_product_image(producto_id, data, filename)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo procesar la imagen: {exc}") from exc
    repository.set_product_image(producto_id, url, now_local().isoformat(timespec="seconds"))
    return {"ok": True, "image": url}


# ------------------------------------------------------------------ código QR

@app.get("/api/admin/qr/info")
def qr_info(request: Request, session=Depends(require_admin)) -> dict[str, Any]:
    url = welcome_url(request)
    return {
        "url": url,
        "nota": "Este enlace es permanente: no cambia al editar el menú.",
        "destino": "Formulario de registro (nunca va directo al menú).",
    }


@app.get("/api/admin/qr")
def qr_code(request: Request, formato: str = "png", session=Depends(require_admin)) -> Response:
    url = welcome_url(request)
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)

    if formato == "svg":
        from qrcode.image.svg import SvgPathImage

        image = qr.make_image(image_factory=SvgPathImage)
        svg = image.to_string().decode("utf-8")
        svg = re.sub(r'fill="(?:black|#000000)"', 'fill="#0b3b2e"', svg, flags=re.IGNORECASE)
        svg = re.sub(r'fill="(?:white|#ffffff)"', 'fill="#ffffff"', svg, flags=re.IGNORECASE)
        if "<svg" in svg and "width=" not in svg.split(">")[0]:
            svg = svg.replace("<svg", '<svg width="512" height="512"', 1)
        return Response(svg, media_type="image/svg+xml",
                        headers={"Content-Disposition": 'attachment; filename="punto_vivo_qr.svg"'})

    if formato != "png":
        raise HTTPException(status_code=400, detail="Formato no soportado. Usa png o svg.")
    image = qr.make_image(fill_color="#0b3b2e", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return Response(buffer.getvalue(), media_type="image/png",
                    headers={"Content-Disposition": 'attachment; filename="punto_vivo_qr.png"'})
