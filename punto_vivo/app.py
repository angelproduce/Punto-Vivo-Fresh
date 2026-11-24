"""PUNTO VIVO FRESH · aplicación FastAPI (público + panel administrativo)."""
from __future__ import annotations

import base64
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
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import admin as admin_tools
from . import push, repository, security, storage, visits
from .config import (
    ADMIN_COOKIE,
    CSRF_HEADER,
    MENU_PATH,
    TRUST_PROXY,
    VISITOR_COOKIE,
    WELCOME_PATH,
    MAX_IMAGE_BYTES,
    MAX_PROOF_BYTES,
    ensure_dirs,
    vapid_keys,
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
    """IP real del visitante detrás de Vercel/Render, para no compartir el límite."""
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
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


def admin_id(session: dict[str, Any]) -> int | None:
    """Id del administrador sesionado, para la auditoría de pagos."""
    admin = repository.get_admin_by_email(session["email"])
    return int(admin["id"]) if admin else None


def verificar_password_admin(session: dict[str, Any], password: str) -> None:
    """Exige la contraseña del administrador que tiene la sesión abierta."""
    admin = repository.get_admin_by_email(session["email"])
    if admin is None or not security.verify_password(password, admin["password_hash"]):
        raise HTTPException(status_code=400, detail="Contraseña incorrecta.")


def require_visitor(request: Request) -> bool:
    return security.verify_visitor(request.cookies.get(VISITOR_COOKIE)) is not None


def visit_payload(request: Request) -> dict[str, Any]:
    payload = security.verify_visitor(request.cookies.get(VISITOR_COOKIE))
    if payload is None:
        raise HTTPException(status_code=401, detail='Registra tu visita para continuar.')
    return payload


def visitor_customer(request: Request) -> tuple[int, int]:
    """Devuelve (visit_id, customer_id) resueltos en servidor desde la cookie firmada."""
    payload = visit_payload(request)
    try:
        visit_id = int(payload['v'])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail='Sesión de visita no válida.')
    customer_id = repository.customer_for_visit(visit_id)
    if customer_id is None:
        raise HTTPException(status_code=401, detail='Registra tu visita para continuar.')
    return visit_id, customer_id


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


def validar_correo_admin(email: str) -> str:
    try:
        return admin_tools.validar_email(email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def validar_clave_admin(password: str) -> None:
    try:
        admin_tools.validar_password(password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def error_correo_duplicado(exc: Exception) -> HTTPException | None:
    texto = str(exc).lower()
    if any(pista in texto for pista in ("ya existe", "duplicate", "23505", "unique violation")):
        return HTTPException(status_code=409, detail="Ya existe un administrador con ese correo.")
    return None


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


class AdminUserIn(BaseModel):
    email: str
    password: str


class AdminUserUpdateIn(BaseModel):
    email: str
    password: str = ""


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


class OrderItemIn(BaseModel):
    product_id: int
    quantity: int
    notes: str = ""


class OrderAddressIn(BaseModel):
    address_line1: str
    address_line2: str = ""
    city: str = ""
    province: str = ""
    notes: str = ""


class OrderIn(BaseModel):
    items: list[OrderItemIn]
    request_token: str
    delivery_method: str = "retiro"
    address: OrderAddressIn | None = None
    notes: str = ""
    payment_method: str = "efectivo"
    bank_account_id: int | None = None


class OrderActionIn(BaseModel):
    accion: str
    referencia: str = ""


class OrderPaymentIn(BaseModel):
    pagado: bool
    confirmado: bool = False
    referencia: str = ""


class BorrarPedidoIn(BaseModel):
    password: str


class PaymentAccountIn(BaseModel):
    banco: str = ""
    titular: str = ""
    numero: str = ""
    tipo: str = ""
    identificacion: str = ""
    instrucciones: str = ""
    activa: bool = False
    orden: int = 0


class PaymentReviewIn(BaseModel):
    decision: str
    nota: str = ""


class PushSubscriptionIn(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


# ------------------------------------------------------------------ páginas públicas

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(WELCOME_PATH, status_code=302)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "app": "PUNTO VIVO FRESH"}


def _web_manifest(app_id: str, nombre: str, corto: str, descripcion: str, inicio: str,
                  scope: str = "/") -> dict:
    """Manifiesto PWA compartido por el menú público y el panel administrativo."""
    return {
        "id": app_id,
        "name": nombre,
        "short_name": corto,
        "description": descripcion,
        "lang": "es",
        "start_url": inicio,
        "scope": scope,
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#f4f8f6",
        "theme_color": "#0b3b2e",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> JSONResponse:
    """PWA del menú: abre el registro y el menú digital."""
    return JSONResponse(
        _web_manifest("/", "PUNTO VIVO FRESH", "Punto Vivo",
                      "Menú digital y control de visitas de PUNTO VIVO FRESH.", WELCOME_PATH),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/admin.webmanifest", include_in_schema=False)
def admin_web_manifest() -> JSONResponse:
    """PWA del panel: el login redirige al panel cuando ya hay sesión iniciada."""
    return JSONResponse(
        _web_manifest("/admin", "PUNTO VIVO FRESH · Panel", "Panel PVF",
                      "Panel administrativo de PUNTO VIVO FRESH.", "/admin/login", scope="/admin"),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    """Service worker con alcance raíz; solo guarda recursos estáticos."""
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get(WELCOME_PATH, response_class=HTMLResponse)
def bienvenida(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "bienvenida.html", public_context(request, menu_path=MENU_PATH)
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
    return templates.TemplateResponse(request, "menu.html", public_context(request))


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


# ------------------------------------------------------------------ pedidos públicos

@app.get('/api/cliente/actual')
def api_cliente_actual(request: Request) -> dict[str, Any]:
    """Nombre y teléfono del visitante, resueltos desde su cookie firmada."""
    visit_id, customer_id = visitor_customer(request)
    cliente = repository.get_customer(customer_id)
    if cliente is None:
        raise HTTPException(status_code=401, detail='Cliente no encontrado.')
    return {
        'nombre': cliente['full_name'], 'telefono': cliente['phone'],
        'visit_id': visit_id, 'cliente_id': customer_id,
    }


@app.post('/api/pedido')
def api_crear_pedido(payload: OrderIn, request: Request) -> dict[str, Any]:
    if not origin_ok(request):
        raise HTTPException(status_code=403, detail='Origen no permitido.')
    if len(payload.request_token.strip()) < 8:
        raise HTTPException(status_code=400, detail='Token de pedido no válido.')
    visit_id, customer_id = visitor_customer(request)
    direccion = payload.address.model_dump() if payload.address else None
    try:
        pedido = repository.create_order(
            visit_id,
            [item.model_dump() for item in payload.items],
            payload.request_token.strip(),
            payload.delivery_method,
            direccion,
            payload.notes,
            payload.payment_method,
            payload.bank_account_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not pedido['duplicate']:
        # La notificación es un extra: un fallo nunca invalida el pedido ya guardado.
        try:
            cliente = repository.get_customer(customer_id)
            push.notificar_nuevo_pedido(
                pedido['order_number'],
                cliente['full_name'] if cliente else 'Cliente',
                pedido['total'],
            )
        except Exception:
            pass
    return {
        'ok': True,
        'numero': pedido['order_number'],
        'pedido_id': pedido['order_id'],
        'total': pedido['total'],
        'duplicate': pedido['duplicate'],
        'mensaje': ('Este pedido ya estaba registrado.' if pedido['duplicate']
                    else 'Pedido confirmado. Te esperamos.'),
    }


@app.get('/api/pedidos/mios')
def api_pedidos_mios(request: Request) -> dict[str, Any]:
    """El cliente solo ve los pedidos ligados a su visita; nunca por ID ajeno."""
    _, customer_id = visitor_customer(request)
    return {'pedidos': repository.customer_orders(customer_id)}


@app.get('/api/pagos/cuentas')
def api_cuentas_publicas() -> dict[str, Any]:
    """Datos bancarios activos y completos; los borradores nunca se publican."""
    return {'cuentas': repository.public_payment_accounts()}


def tipo_comprobante(nombre: str, datos: bytes) -> str:
    """Valida extensión y cabecera real: JPG, JPEG, PNG o PDF, hasta 5 MB."""
    extension = Path(nombre or '').suffix.lower()
    firmas = {
        '.jpg': (b'\xff\xd8\xff', 'image/jpeg'),
        '.jpeg': (b'\xff\xd8\xff', 'image/jpeg'),
        '.png': (b'\x89PNG\r\n\x1a\n', 'image/png'),
        '.pdf': (b'%PDF-', 'application/pdf'),
    }
    permitido = firmas.get(extension)
    if permitido is None or not datos.startswith(permitido[0]):
        raise ValueError('El comprobante debe ser JPG, JPEG, PNG o PDF.')
    if not 0 < len(datos) <= MAX_PROOF_BYTES:
        raise ValueError(f'El comprobante no puede superar {MAX_PROOF_BYTES // (1024 * 1024)} MB.')
    return permitido[1]


@app.post('/api/pedidos/{pedido_id}/comprobante')
async def api_subir_comprobante(pedido_id: int, request: Request,
                                file: UploadFile = File(...)) -> dict[str, Any]:
    """El cliente sube su comprobante; eso nunca confirma el pago."""
    if not origin_ok(request):
        raise HTTPException(status_code=403, detail='Origen no permitido.')
    _, customer_id = visitor_customer(request)
    pedido = repository.get_order(pedido_id)
    if pedido is None or pedido['cliente_id'] != customer_id:
        raise HTTPException(status_code=404, detail='Pedido no encontrado.')
    pago = pedido['pago'] or {}
    if pago.get('metodo') != 'transferencia':
        raise HTTPException(status_code=400, detail='Este pedido no es por transferencia.')
    if pago.get('estado', 'pendiente') not in repository.ESTADOS_CON_COMPROBANTE:
        raise HTTPException(status_code=400, detail='Este pago ya no admite comprobantes.')
    datos = await file.read(MAX_PROOF_BYTES + 1)
    try:
        content_type = tipo_comprobante(file.filename or '', datos)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    extension = Path(file.filename or '').suffix.lower()
    nombre = f'comprobante-{pedido_id}-{uuid4().hex}{extension}'
    ruta = storage.save_private_file(nombre, datos, content_type)
    anterior = repository.get_payment_proof(pedido_id)
    try:
        repository.save_payment_proof(
            pedido_id, customer_id, pedido.get('banco_id'),
            Path(file.filename or nombre).name[:120], ruta, content_type, len(datos),
        )
        actualizado = repository.mark_proof_received(pedido_id)
    except Exception:
        if anterior is None:
            storage.delete_private_file(ruta)
        raise HTTPException(status_code=400, detail='No se pudo guardar el comprobante.')
    return {
        'ok': True, 'pago_estado': actualizado['pago']['estado'],
        'mensaje': 'Comprobante recibido. El negocio debe verificar la transferencia.',
    }


# ------------------------------------------------------------------ autenticación admin

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request) -> Response:
    if security.get_session(request.cookies.get(ADMIN_COOKIE)):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse(request, "admin_login.html", public_context(request))


@app.post("/api/admin/login")
def api_admin_login(payload: LoginIn, request: Request) -> JSONResponse:
    if not origin_ok(request):
        raise HTTPException(status_code=403, detail="Origen no permitido.")
    client_hash = security.ip_hash(client_ip(request))
    if security.login_bloqueado(client_hash):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espera una hora.")

    row = repository.get_admin_by_email(payload.email.strip().lower())
    if row is None or not security.verify_password(payload.password, row["password_hash"]):
        security.registrar_fallo_login(client_hash)
        raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos.")

    security.limpiar_intentos_login(client_hash)
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
    return {
        "id": session["admin_id"], "email": session["email"],
        "role": session["role"], "csrf_token": session["csrf_token"],
    }


# ------------------------------------------------------------------ usuarios del panel

@app.get("/api/admin/usuarios")
def api_usuarios(session=Depends(require_admin)) -> dict[str, Any]:
    return {
        "usuarios": [dict(row) for row in repository.list_admins()],
        "mi_id": session["admin_id"],
    }


@app.post("/api/admin/usuarios")
def crear_usuario(payload: AdminUserIn, session=Depends(require_admin)) -> dict[str, Any]:
    email = validar_correo_admin(payload.email)
    validar_clave_admin(payload.password)
    if repository.get_admin_by_email(email):
        raise HTTPException(status_code=409, detail="Ya existe un administrador con ese correo.")
    try:
        new_id = admin_tools.create_admin(email, payload.password)
    except Exception as exc:
        duplicado = error_correo_duplicado(exc)
        if duplicado is not None:
            raise duplicado
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc))
        raise
    return {"ok": True, "id": new_id}


@app.put("/api/admin/usuarios/{admin_id}")
def editar_usuario(admin_id: int, payload: AdminUserUpdateIn,
                   session=Depends(require_admin)) -> dict[str, Any]:
    if repository.get_admin_by_id(admin_id) is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    email = validar_correo_admin(payload.email)
    otro = repository.get_admin_by_email(email)
    if otro is not None and int(otro["id"]) != admin_id:
        raise HTTPException(status_code=409, detail="Ya existe un administrador con ese correo.")

    es_yo = int(session["admin_id"]) == admin_id
    cambia_clave = bool(payload.password)
    if cambia_clave:
        validar_clave_admin(payload.password)
    try:
        repository.update_admin_email(admin_id, email)
        if cambia_clave:
            repository.change_admin_password(admin_id, security.hash_password(payload.password))
            repository.delete_sessions_by_admin(admin_id)
    except Exception as exc:
        duplicado = error_correo_duplicado(exc)
        if duplicado is not None:
            raise duplicado
        raise
    return {"ok": True, "email": email, "cerrar_sesion": es_yo and cambia_clave}


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> Response:
    session = security.get_session(request.cookies.get(ADMIN_COOKIE))
    if session is None:
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse(
        request, "admin.html", public_context(
            request, admin_email=session["email"], admin_id=session["admin_id"],
            csrf=session["csrf_token"],
        )
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
    ventas = repository.sales_summary()

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
        "pedidos_totales": ventas["total_pedidos"],
        "pedidos_pendientes": ventas["pendientes_pago"],
        "ventas_hoy": ventas["ventas_hoy"],
        "ventas_semana": ventas["ventas_semana"],
        "ventas_mes": ventas["ventas_mes"],
        "total_ventas": ventas["total_ventas"],
        "monto_pendiente": ventas["monto_pendiente"],
    }


# ------------------------------------------------------------------ pedidos admin

def filtro_pedidos(q: str, estado: str, pago: str, desde: str, hasta: str) -> dict[str, str]:
    return {"q": q, "estado": estado, "pago": pago, "desde": desde, "hasta": hasta}


@app.get("/api/admin/pedidos/nuevos")
def api_pedidos_nuevos(session=Depends(require_admin)) -> dict[str, Any]:
    pedidos = repository.list_orders({"estado": "pendiente"}, 30)
    return {"total": len(pedidos), "pedidos": pedidos}


@app.get("/api/admin/pedidos")
def api_pedidos(q: str = "", estado: str = "", pago: str = "", desde: str = "", hasta: str = "",
                limite: int = 200, session=Depends(require_admin)) -> dict[str, Any]:
    filters = filtro_pedidos(q, estado, pago, desde, hasta)
    pedidos = repository.list_orders(filters, min(max(limite, 1), 1000))
    return {"total": repository.count_orders(filters), "pedidos": pedidos}


@app.get("/api/admin/pedidos/{pedido_id}")
def api_pedido_detalle(pedido_id: int, session=Depends(require_admin)) -> dict[str, Any]:
    pedido = repository.get_order(pedido_id)
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    return pedido


@app.patch("/api/admin/pedidos/{pedido_id}/estado")
def api_pedido_estado(pedido_id: int, payload: OrderActionIn,
                      session=Depends(require_admin)) -> dict[str, Any]:
    try:
        pedido = repository.update_order_status(pedido_id, payload.accion, payload.referencia)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    return pedido


@app.patch("/api/admin/pedidos/{pedido_id}/pago")
def api_pedido_pago(pedido_id: int, payload: OrderPaymentIn,
                    session=Depends(require_admin)) -> dict[str, Any]:
    """Confirmación explícita del admin; el navegador del cliente nunca llama aquí."""
    if not payload.confirmado:
        raise HTTPException(status_code=400, detail="Confirma la acción para continuar.")
    try:
        pedido = repository.set_order_paid(pedido_id, payload.pagado, payload.referencia,
                                           admin_id(session))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    return pedido


@app.patch("/api/admin/pedidos/{pedido_id}/revision")
def api_pedido_revision(pedido_id: int, payload: PaymentReviewIn,
                        session=Depends(require_admin)) -> dict[str, Any]:
    """Verificación explícita del administrador, con nota y auditoría."""
    autor = admin_id(session)
    if autor is None:
        raise HTTPException(status_code=401, detail="Administrador no encontrado.")
    try:
        pedido = repository.review_payment(pedido_id, payload.decision, autor, payload.nota)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if pedido is None:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    return pedido


@app.get("/api/admin/pedidos/{pedido_id}/comprobante")
def api_comprobante_admin(pedido_id: int, session=Depends(require_admin)) -> dict[str, Any]:
    """Comprobante privado: nunca se sirve desde /static ni sin sesión admin."""
    proof = repository.get_payment_proof(pedido_id)
    if proof is None:
        raise HTTPException(status_code=404, detail="Este pedido no tiene comprobante.")
    datos = ''
    url = ''
    try:
        url = storage.signed_proof_url(proof['ruta'])
        if not url:
            contenido = storage.read_private_file(proof['ruta'])
            datos = 'data:' + proof['tipo'] + ';base64,' + base64.b64encode(contenido).decode('ascii')
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo leer el comprobante.") from exc
    return {
        'nombre': proof['nombre'], 'tipo': proof['tipo'], 'tamano': proof['tamano'],
        'enviado': proof['enviado'], 'url': url, 'datos': datos,
    }


@app.delete("/api/admin/pedidos/{pedido_id}/comprobante")
def api_comprobante_borrar(pedido_id: int, payload: BorrarPedidoIn,
                           session=Depends(require_admin)) -> dict[str, Any]:
    """Borra el archivo privado y su registro; jamás cambia el estado del pago."""
    verificar_password_admin(session, payload.password)
    prueba = repository.get_payment_proof(pedido_id)
    if prueba is None:
        raise HTTPException(status_code=404, detail="Este pedido no tiene comprobante.")
    try:
        storage.delete_private_file(prueba['ruta'])
    except Exception as exc:
        raise HTTPException(status_code=502, detail="No se pudo borrar el comprobante.") from exc
    repository.delete_payment_proof(prueba['id'])
    return {'eliminado': 1, 'pedido_id': pedido_id, 'nombre': prueba['nombre']}


@app.get("/api/admin/pagos/transferencias")
def api_transferencias(session=Depends(require_admin)) -> dict[str, Any]:
    pedidos = [p for p in repository.list_orders(limit=1000)
               if p['metodo_pago'] == 'transferencia']
    return {'total': len(pedidos), 'pedidos': pedidos}


@app.get("/api/admin/pagos/cuentas")
def api_cuentas_admin(session=Depends(require_admin)) -> dict[str, Any]:
    return {'cuentas': repository.list_payment_accounts()}


@app.post("/api/admin/pagos/cuentas")
def api_crear_cuenta(payload: PaymentAccountIn, session=Depends(require_admin)) -> dict[str, Any]:
    try:
        return repository.create_payment_account(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/admin/pagos/cuentas/{cuenta_id}")
def api_editar_cuenta(cuenta_id: int, payload: PaymentAccountIn,
                      session=Depends(require_admin)) -> dict[str, Any]:
    try:
        cuenta = repository.update_payment_account(cuenta_id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if cuenta is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    return cuenta


@app.delete("/api/admin/pagos/cuentas/{cuenta_id}")
def api_borrar_cuenta(cuenta_id: int, session=Depends(require_admin)) -> dict[str, Any]:
    if not repository.delete_payment_account(cuenta_id):
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    return {'eliminada': cuenta_id}


@app.get("/api/admin/push/clave")
def api_push_clave(session=Depends(require_admin)) -> dict[str, Any]:
    publica, _ = vapid_keys()
    return {'publica': publica, 'configurado': bool(publica)}


@app.post("/api/admin/push/suscribir")
def api_push_suscribir(payload: PushSubscriptionIn,
                       session=Depends(require_admin)) -> dict[str, Any]:
    if not payload.endpoint.startswith('https://'):
        raise HTTPException(status_code=400, detail="Suscripción no válida.")
    autor = admin_id(session)
    if autor is None:
        raise HTTPException(status_code=401, detail="Administrador no encontrado.")
    repository.add_push_subscription(autor, payload.endpoint[:1000],
                                     payload.p256dh[:500], payload.auth[:500])
    return {'ok': True}


@app.delete("/api/admin/push/suscribir")
def api_push_desuscribir(payload: PushSubscriptionIn,
                         session=Depends(require_admin)) -> dict[str, Any]:
    borradas = repository.remove_push_subscription(payload.endpoint[:1000])
    return {'ok': True, 'eliminadas': borradas}


@app.delete("/api/admin/pedidos/{pedido_id}")
def api_pedido_borrar(pedido_id: int, payload: BorrarPedidoIn,
                      session=Depends(require_admin)) -> dict[str, Any]:
    """Borrado definitivo de un pedido, solo tras confirmar la contraseña del admin."""
    verificar_password_admin(session, payload.password)
    prueba = repository.get_payment_proof(pedido_id)
    borrado = repository.delete_order(pedido_id)
    if borrado is None:
        raise HTTPException(status_code=404, detail="Pedido no encontrado.")
    if prueba is not None:
        # El registro ya se borró en cascada; si el archivo falla, no queda dato expuesto.
        try:
            storage.delete_private_file(prueba['ruta'])
        except Exception:
            pass
    return borrado


@app.get("/api/admin/ventas")
def api_ventas(session=Depends(require_admin)) -> dict[str, Any]:
    return repository.sales_summary()


@app.get("/api/admin/exportar/pedidos.csv")
def exportar_pedidos(q: str = "", estado: str = "", pago: str = "", desde: str = "", hasta: str = "",
                     session=Depends(require_admin)) -> Response:
    pedidos = repository.list_orders(filtro_pedidos(q, estado, pago, desde, hasta))
    filas = []
    for pedido in pedidos:
        detalle = repository.get_order(pedido["id"]) or pedido
        productos = "; ".join(
            f"{i['nombre']} x{i['cantidad']}" for i in detalle.get("items", [])
        )
        filas.append([
            pedido["numero"], pedido["cliente"], pedido["telefono"], pedido["entrega"],
            pedido["creado"], pedido["estado"], pedido["pago_estado"], pedido["metodo_pago"],
            pedido["subtotal"], pedido["descuento"], pedido["envio"], pedido["total"],
            productos, pedido["notas"],
        ])
    return csv_response(
        "punto_vivo_pedidos.csv",
        ["N.º pedido", "Cliente", "Teléfono", "Entrega", "Fecha", "Estado", "Pago",
         "Método de pago", "Subtotal", "Descuento", "Envío", "Total", "Productos", "Observaciones"],
        filas,
    )


@app.get("/api/admin/exportar/ventas.csv")
def exportar_ventas(session=Depends(require_admin)) -> Response:
    filas = []
    for pedido in repository.paid_orders():
        filas.append([
            pedido["numero"], pedido["cliente"], pedido["telefono"], pedido["creado"],
            pedido["estado"], pedido["total"], pedido["metodo_pago"],
        ])
    return csv_response(
        "punto_vivo_ventas.csv",
        ["N.º pedido", "Cliente", "Teléfono", "Fecha", "Estado", "Total", "Método de pago"],
        filas,
    )


@app.get("/api/admin/clientes/frecuentes")
def api_clientes_frecuentes(session=Depends(require_admin)) -> dict[str, Any]:
    return {"clientes": repository.frequent_customers(10)}


@app.get("/api/admin/clientes/{cliente_id}/perfil")
def api_cliente_perfil(cliente_id: int, session=Depends(require_admin)) -> dict[str, Any]:
    perfil = repository.customer_profile(cliente_id)
    if perfil is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    return perfil


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
        "cliente": {"id": cliente_id, "nombre": cliente["full_name"], "telefono": cliente["phone"]},
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


@app.delete("/api/admin/visitas")
def api_eliminar_visitas(q: str = "", desde: str = "", hasta: str = "",
                         session=Depends(require_admin)) -> dict[str, int]:
    """Elimina las visitas del filtro actual o todas si no hay filtro."""
    return {"eliminadas": repository.delete_visits(filtro_visitas(q, desde, hasta))}


@app.delete("/api/admin/visitas/{visita_id}")
def api_eliminar_visita(visita_id: int, session=Depends(require_admin)) -> dict[str, int]:
    eliminadas = repository.delete_visit(visita_id)
    if not eliminadas:
        raise HTTPException(status_code=404, detail="Visita no encontrada.")
    return {"eliminadas": eliminadas}


@app.delete("/api/admin/clientes/{cliente_id}/visitas")
def api_eliminar_historial(cliente_id: int, session=Depends(require_admin)) -> dict[str, int]:
    if repository.get_customer(cliente_id) is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    return {"eliminadas": repository.delete_customer_visits(cliente_id)}


@app.delete("/api/admin/clientes/{cliente_id}")
def api_eliminar_cliente(cliente_id: int, session=Depends(require_admin)) -> dict[str, int]:
    """Elimina el cliente y todas sus visitas en una sola operación."""
    try:
        visitas = repository.delete_customer(cliente_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if visitas is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    return {"eliminado": 1, "visitas": visitas}


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
    try:
        repository.delete_product(producto_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
