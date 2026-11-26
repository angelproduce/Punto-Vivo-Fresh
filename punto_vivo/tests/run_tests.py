"""Pruebas obligatorias de PUNTO VIVO FRESH (se ejecutan con pytest o directamente)."""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:  # pragma: no cover
    pass

TMP = tempfile.mkdtemp(prefix="pv_tests_")
os.environ["PV_DB_PATH"] = os.path.join(TMP, "tests.sqlite3")
os.environ["PV_SECRET_KEY"] = "clave-de-prueba-no-produccion-123456"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from punto_vivo import admin, db, repository, storage  # noqa: E402
from punto_vivo.app import app  # noqa: E402

client = TestClient(app)
ADMIN_EMAIL = "admin@pruebas.local"
ADMIN_PASS = "ClaveDePruebas123!"
CSRF = ""
passed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if not condition:
        raise AssertionError(f"FALLÓ: {name} {detail}")
    passed += 1
    print(f"  [{passed:02d}] OK  {name}")


def admin_headers() -> dict[str, str]:
    return {"X-CSRF-Token": CSRF}


def reset_ventana() -> None:
    conn = db.connect()
    conn.execute("UPDATE visits SET created_epoch = created_epoch - 3600")
    conn.execute("UPDATE rate_events SET created_epoch = created_epoch - 7200")
    conn.close()


def limpiar_intentos_login() -> None:
    conn = db.connect()
    conn.execute("DELETE FROM rate_events WHERE scope='login_ip'")
    conn.commit()
    conn.close()


def eventos_login() -> int:
    conn = db.connect()
    total = conn.execute("SELECT COUNT(*) FROM rate_events WHERE scope='login_ip'").fetchone()[0]
    conn.close()
    return total


def registrar(nombre: str, telefono: str, token: str):
    return client.post("/api/visita", json={
        "full_name": nombre, "phone": telefono, "consent": True,
        "request_token": token, "website": "",
    })


def run() -> None:
    global CSRF
    db.init_db()
    admin.create_admin(ADMIN_EMAIL, ADMIN_PASS)
    print("\nPUNTO VIVO FRESH · batería de pruebas\n" + "-" * 52)

    # 1 y 6: registro de cliente nuevo + redirección/menu
    r = registrar("Juan Pérez", "809 000 0000", "token-nuevo-1234567890")
    check("1. Registro de cliente nuevo", r.status_code == 200 and r.json()["visit_number"] == 1, r.text)
    check("6a. Respuesta incluye menú y cookie", r.json()["menu_url"] == "/menu" and "pv_visita" in r.cookies)
    check("6b. /menu accesible tras registrar", client.get("/menu").status_code == 200)
    check("6c. /api/menu devuelve productos", len(client.get("/api/menu").json()["productos"]) == 12)

    # 3: incremento con cliente existente (mismo teléfono distinto formato)
    reset_ventana()
    r2 = registrar("Juan Perez", "(809) 000-0000", "token-existente-12345678")
    check("2/3. Cliente existente → visita #2", r2.status_code == 200 and r2.json()["visit_number"] == 2, r2.text)
    check("3b. No se creó otro cliente",
          db.one("SELECT COUNT(*) FROM customers")[0] == 1)
    check("3c. Teléfono normalizado", db.one("SELECT phone FROM customers")[0] == "8090000000")

    # 4: fecha, hora y registro exacto
    row = db.one("SELECT * FROM visits ORDER BY id DESC LIMIT 1")
    check("4a. Fecha YYYY-MM-DD", re.match(r"^\d{4}-\d{2}-\d{2}$", row["visit_date"]) is not None)
    check("4b. Hora HH:MM:SS", re.match(r"^\d{2}:\d{2}:\d{2}$", row["visit_time"]) is not None)
    check("4c. Registro exacto ISO", "T" in row["created_at"])

    # 5: doble envío (mismo token y ventana corta)
    r3 = registrar("Juan Perez", "8090000000", "token-existente-12345678")
    total_antes = db.one("SELECT COUNT(*) FROM visits")[0]
    r4 = registrar("Juan Perez", "8090000000", "otro-token-1234567890")
    check("5a. Mismo token no duplica", r3.json()["duplicate"] is True)
    check("5b. Doble clic en 20 s no duplica", r4.json()["duplicate"] is True)
    check("5c. Total de visitas intacto", db.one("SELECT COUNT(*) FROM visits")[0] == total_antes)

    # Consentimiento y validación
    sin_consent = client.post("/api/visita", json={
        "full_name": "Ana Díaz", "phone": "8295551234", "consent": False,
        "request_token": "token-sin-consent-1234", "website": ""})
    check("9a. Consentimiento obligatorio", sin_consent.status_code == 400)
    mal = registrar("A", "123", "token-malo-12345678901")
    check("9b. Validación servidor rechaza datos malos", mal.status_code == 400)

    # 6d: sin registro no se puede entrar al menú (cliente limpio)
    anonimo = TestClient(app)
    check("6d. /menu sin registro redirige", anonimo.get("/menu", follow_redirects=False).status_code in (302, 307))
    check("6e. /api/menu sin registro → 401", anonimo.get("/api/menu").status_code == 401)

    # 8: login administrativo y CSRF
    check("8a. Panel protegido", anonimo.get("/admin", follow_redirects=False).status_code in (302, 307))
    bad = anonimo.post("/api/admin/login", json={"email": ADMIN_EMAIL, "password": "incorrecta"})
    check("8b. Login con contraseña incorrecta", bad.status_code == 401)
    login = anonimo.post("/api/admin/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    check("8c. Login correcto", login.status_code == 200, login.text)
    CSRF = login.json()["csrf_token"]
    check("8d. Endpoint admin requiere CSRF",
          anonimo.get("/api/admin/resumen").status_code == 200 and
          anonimo.post("/api/admin/logout").status_code == 403)
    resumen = anonimo.get("/api/admin/resumen").json()
    check("8e. Resumen con totales", resumen["total_clientes"] == 1 and resumen["total_visitas"] == 2)

    # 8f: gestión de usuarios y contraseñas desde el panel
    listado = anonimo.get("/api/admin/usuarios", headers=admin_headers())
    check("8f. Usuarios sin hash de contraseña",
          listado.status_code == 200 and all("password_hash" not in u for u in listado.json()["usuarios"]))
    nuevo = anonimo.post("/api/admin/usuarios", headers=admin_headers(),
                         json={"email": "segundo@pruebas.local", "password": "ClaveSegunda123!"})
    check("8g. Crear usuario", nuevo.status_code == 200, nuevo.text)
    duplicado = anonimo.post("/api/admin/usuarios", headers=admin_headers(),
                             json={"email": "segundo@pruebas.local", "password": "OtraClave123!"})
    check("8h. Correo duplicado rechazado", duplicado.status_code == 409)
    corta = anonimo.post("/api/admin/usuarios", headers=admin_headers(),
                         json={"email": "tercero@pruebas.local", "password": "corta"})
    check("8i. Contraseña corta rechazada", corta.status_code == 400)
    uid = nuevo.json()["id"]
    editado = anonimo.put(f"/api/admin/usuarios/{uid}", headers=admin_headers(),
                          json={"email": "segundo2@pruebas.local", "password": "ClaveNueva123!"})
    check("8j. Cambiar correo y contraseña", editado.status_code == 200, editado.text)
    otro = TestClient(app)
    acceso = otro.post("/api/admin/login",
                       json={"email": "segundo2@pruebas.local", "password": "ClaveNueva123!"})
    check("8k. El nuevo usuario puede entrar", acceso.status_code == 200, acceso.text)
    id_otro = otro.get("/api/admin/usuarios").json()["mi_id"]
    headers_otro = {"X-CSRF-Token": acceso.json()["csrf_token"]}
    propio = otro.put(f"/api/admin/usuarios/{id_otro}", headers=headers_otro,
                      json={"email": "segundo2@pruebas.local", "password": "ClavePropia123!"})
    check("8l. Cambiar contraseña propia sin contraseña anterior",
          propio.status_code == 200 and propio.json().get("cerrar_sesion") is True, propio.text)
    verificar = TestClient(app)
    acceso_nuevo = verificar.post("/api/admin/login",
                                  json={"email": "segundo2@pruebas.local", "password": "ClavePropia123!"})
    check("8m. La nueva contraseña propia funciona", acceso_nuevo.status_code == 200, acceso_nuevo.text)

    # 8n: el límite cuenta solo fallos y un login correcto lo limpia
    limpiar_intentos_login()
    cliente_limite = TestClient(app)
    estados = []
    for _ in range(11):
        fallo = cliente_limite.post("/api/admin/login", json={"email": ADMIN_EMAIL, "password": "clave-mala"})
        estados.append(fallo.status_code)
    check("8n. Diez fallos permitidos y el undécimo bloqueado",
          estados[:10] == [401] * 10 and estados[10] == 429, str(estados))
    check("8o. Quedan registrados 10 fallos", eventos_login() == 10)
    correcta_bloqueada = cliente_limite.post(
        "/api/admin/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    check("8p. Login correcto sigue bloqueado hasta limpiar", correcta_bloqueada.status_code == 429)
    limpiar_intentos_login()
    desbloqueada = cliente_limite.post(
        "/api/admin/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    check("8q. Login correcto no consume intentos y limpia los fallos",
          desbloqueada.status_code == 200 and eventos_login() == 0, desbloqueada.text)

    # 7: QR siempre al formulario + formatos
    info = anonimo.get("/api/admin/qr/info", headers=admin_headers()).json()
    check("7a. QR apunta al registro", info["url"].endswith("/bienvenida"))
    png = anonimo.get("/api/admin/qr?formato=png", headers=admin_headers())
    svg = anonimo.get("/api/admin/qr?formato=svg", headers=admin_headers())
    check("7b. Descarga PNG válida", png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n")
    check("7c. Descarga SVG válida", svg.status_code == 200 and b"<svg" in svg.content and b"#0b3b2e" in svg.content)

    # 9: crear y modificar productos; reflejo en el menú público
    cats = anonimo.get("/api/admin/categorias", headers=admin_headers()).json()["categorias"]
    cat_id = cats[0]["id"]
    created = anonimo.post("/api/admin/productos", headers=admin_headers(), json={
        "category_id": cat_id, "name": "Producto de prueba", "description": "Prueba automatizada",
        "price": 125.50, "image": "", "available": True, "is_demo": False, "sort_order": 99})
    check("9c. Crear producto", created.status_code == 200, created.text)
    pid = created.json()["id"]
    edit = anonimo.put(f"/api/admin/productos/{pid}", headers=admin_headers(), json={
        "category_id": cat_id, "name": "Producto editado", "description": "Editado",
        "price": 150, "image": "", "available": True, "is_demo": False, "sort_order": 99})
    check("9d. Editar producto", edit.status_code == 200)
    public = client.get("/api/menu").json()["productos"]
    check("9e. Cambio visible en menú público",
          any(p["name"] == "Producto editado" and p["price_label"] == "RD$ 150.00" for p in public))
    info_despues = anonimo.get("/api/admin/qr/info", headers=admin_headers()).json()
    check("7d. Enlace QR estable tras cambios en el menú", info_despues["url"] == info["url"])

    # Subida de imagen (WebP generado por Pillow)
    from PIL import Image
    img = Image.new("RGB", (40, 40), "green")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    up = anonimo.post(f"/api/admin/productos/{pid}/imagen", headers=admin_headers(),
                      files={"file": ("prueba.png", buf.getvalue(), "image/png")})
    check("9f. Subida de imagen optimizada", up.status_code == 200 and up.json()["image"].endswith(".webp"), up.text)

    # 10: exportación CSV
    export = anonimo.get("/api/admin/exportar/visitas.csv", headers=admin_headers())
    text = export.content.decode("utf-8-sig")
    check("10a. CSV de visitas", export.status_code == 200 and "N.º visita" in text and "Juan Pérez" in text)
    clientes_csv = anonimo.get("/api/admin/exportar/clientes.csv", headers=admin_headers())
    check("10b. CSV de clientes", "Total visitas" in clientes_csv.content.decode("utf-8-sig"))

    # Filtros y búsqueda
    filtradas = anonimo.get("/api/admin/visitas?q=8090000000&desde=2020-01-01&hasta=2099-12-31",
                            headers=admin_headers()).json()
    check("11. Búsqueda y filtro de fechas", filtradas["total"] == 2)

    # Limpieza del producto de prueba
    anonimo.delete(f"/api/admin/productos/{pid}", headers=admin_headers())

    check("35. Logo presente en la interfaz", "/static/logo.png" in anonimo.get("/bienvenida").text)
    check("36. Aviso de demostración ausente del menú",
          "Datos de demostración" not in client.get("/menu").text)
    check("37. Titular del menú actualizado",
          "Pregunta por el <span>combo</span> de hoy" in client.get("/menu").text)
    check("38. Pie del menú actualizado",
          "Hecho al momento pensando en ti." in client.get("/menu").text)
    check("39. Texto de fidelidad en bienvenida",
          "Por tu fidelidad te premiamos." in anonimo.get("/bienvenida").text)
    _menu_html = client.get("/menu").text
    check("40. Botón de vista móvil/escritorio en el menú", 'id="mode-toggle"' in _menu_html)
    check("41. Contenedor de vista del menú", 'id="menu-viewport"' in _menu_html)
    _base_ui = Path(__file__).resolve().parents[1]
    _admin_tpl = (_base_ui / "templates" / "admin.html").read_text(encoding="utf-8")
    _admin_js = (_base_ui / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    check("42. Campo de contraseña preservado en el modal de usuarios",
          'id="user-password-title"' in _admin_tpl and 'id="user-password"' in _admin_tpl
          and "user-password-title" in _admin_js and "user-password-label" not in _admin_js)
    _admin_html = anonimo.get("/admin").text
    _admin_css = (_base_ui / "static" / "css" / "styles.css").read_text(encoding="utf-8")
    check("43. Selector de vista móvil/escritorio en el panel",
          'id="admin-mode-toggle"' in _admin_html and "pv_admin_mode" in _admin_js
          and "body.pv-mobile .admin-shell" in _admin_css
          and "body.pv-desktop .admin-shell" in _admin_css)
    _manifest = anonimo.get("/manifest.webmanifest")
    _manifest_data = _manifest.json() if _manifest.status_code == 200 else {}
    _sizes = {icon.get("sizes") for icon in _manifest_data.get("icons", [])}
    check("44. Manifiesto PWA para instalar",
          _manifest.status_code == 200 and _manifest_data.get("display") == "standalone"
          and _manifest_data.get("start_url") == "/bienvenida" and {"192x192", "512x512"} <= _sizes
          and "application/manifest+json" in _manifest.headers.get("content-type", ""))
    _sw = anonimo.get("/sw.js")
    check("45. Service worker con alcance raíz",
          _sw.status_code == 200 and _sw.headers.get("service-worker-allowed") == "/"
          and "addEventListener('fetch'" in _sw.text)
    _icons_dir = _base_ui / "static" / "icons"
    check("46. Iconos de instalación presentes",
          all((_icons_dir / ("icon-%d.png" % size)).stat().st_size > 0 for size in (192, 512)))
    _base_tpl = (_base_ui / "templates" / "base.html").read_text(encoding="utf-8")
    _menu_tpl = (_base_ui / "templates" / "menu.html").read_text(encoding="utf-8")
    _admin_login_tpl = (_base_ui / "templates" / "admin_login.html").read_text(encoding="utf-8")
    _public_js = (_base_ui / "static" / "js" / "public.js").read_text(encoding="utf-8")
    _install_js = (_base_ui / "static" / "js" / "install.js").read_text(encoding="utf-8")
    check("47. Registro PWA y botones de instalación",
          'rel="manifest"' in _base_tpl and "serviceWorker.register('/sw.js')" in _base_tpl
          and 'id="install-app"' in _menu_tpl and 'data-install-app' in _menu_tpl
          and 'id="admin-install-app"' in _admin_tpl and 'data-install-app' in _admin_tpl
          and 'id="admin-login-install-app"' in _admin_login_tpl and 'data-install-app' in _admin_login_tpl
          and "beforeinstallprompt" in _install_js and 'src="/static/js/install.js"' in _base_tpl)
    check("48. Panel usa modo móvil automático en teléfono",
          "document.body.classList.toggle('pv-mobile', movil)" in _admin_js
          and "if (modo === 'auto') aplicarModo('auto')" in _admin_js)
    check("49. Modo móvil ocupa todo el ancho en teléfono",
          "body.pv-mobile .menu-viewport,body.pv-mobile .admin-shell{max-width:none" in _admin_css
          and "@media(max-width:700px)" in _admin_css)
    check("50. Navegación inferior compacta en el panel",
          "grid-template-columns:repeat(6,minmax(0,1fr))" in _admin_css
          and "content:attr(data-short)" in _admin_css
          and 'data-short="Menú"' in _admin_tpl
          and 'id="btn-logout-movil"' in _admin_html)
    check("50b. Menú del panel con desplazamiento vertical y horizontal",
          ".admin-nav{display:flex;flex-direction:column;gap:5px;min-height:0;overflow-y:auto" in _admin_css
          and "body.pv-mobile .admin-nav{display:flex;flex-wrap:nowrap;grid-template-columns:none;overflow-x:auto" in _admin_css
          and "nav.scrollTo" in _admin_js)
    check("51. Tablas convertidas en tarjetas en móvil",
          "body.pv-mobile .data-table thead{display:none}" in _admin_css
          and "content:attr(data-label)" in _admin_css
          and "MutationObserver" in _admin_js and "dataset.label" in _admin_js)
    check("52. Modales tipo hoja y menú denso en móvil",
          "body.pv-mobile .modal-backdrop{align-items:end;padding:0}" in _admin_css
          and "body.pv-mobile .product-grid{grid-template-columns:repeat(2" in _admin_css
          and "dialog.dialog-product" in _admin_css)
    check("53. El modo móvil conserva menú y cierre de sesión",
          'class="btn btn-ghost btn-sm solo-movil" href="/menu"' in _admin_html
          and "$('btn-logout-movil')?.addEventListener" in _admin_js)
    _admin_manifest = anonimo.get("/admin.webmanifest")
    _admin_manifest_data = _admin_manifest.json() if _admin_manifest.status_code == 200 else {}
    _admin_sizes = {icon.get("sizes") for icon in _admin_manifest_data.get("icons", [])}
    _anonimo_pwa = TestClient(app)
    _admin_login = _anonimo_pwa.get("/admin/login", follow_redirects=False)
    check("54. Manifiesto del panel es instalable sin sesión",
          _admin_manifest.status_code == 200 and _admin_manifest_data.get("id") == "/admin"
          and _admin_manifest_data.get("start_url") == "/admin/login"
          and _admin_manifest_data.get("scope") == "/admin"
          and _admin_manifest_data.get("display") == "standalone" and _sizes <= _admin_sizes
          and "application/manifest+json" in _admin_manifest.headers.get("content-type", "")
          and _admin_login.status_code == 200)
    check("55. El panel y su acceso enlazan su propio manifiesto",
          '{% block manifest_url %}/admin.webmanifest{% endblock %}' in _admin_tpl
          and '{% block manifest_url %}/admin.webmanifest{% endblock %}' in _admin_login_tpl
          and '{% block manifest_url %}' in _base_tpl)
    check("56. Service worker incluye el instalador compartido",
          "'/static/js/install.js'" in _sw.text and "pvf-static-20261126f" in _sw.text
          and "'/static/audio/alerta-fuerte.wav'" in _sw.text
          and "sound: '/static/audio/alerta-fuerte.wav'" in _sw.text
          and "silent: false" in _sw.text
          and (_base_ui / "static" / "audio" / "alerta-fuerte.wav").exists())
    check("57. Buscador y filtros siempre visibles, sin plegable",
          'id="filter-toggle"' not in _menu_tpl and 'id="filter-panel"' not in _menu_tpl
          and 'id="search"' in _menu_tpl and 'id="cat-chips"' in _menu_tpl
          and "pv_menu_filtros" not in _public_js and "filtroPanel.hidden" not in _public_js)
    check("58. Búsqueda y selección de categorías siguen funcionando",
          "getElementById('search').addEventListener" in _public_js
          and "state.q = e.target.value" in _public_js
          and "state.categoria = chip.dataset.cat" in _public_js)
    check("59. Sin estilos ni caché del plegable",
          ".filter-panel[hidden]" not in _admin_css and ".filter-toggle" not in _admin_css
          and "pvf-static-20261123i" not in _sw.text and "pvf-static-20261124c" not in _sw.text
          and "pvf-static-20261124d" not in _sw.text and "pvf-static-20261124e" not in _sw.text
          and "pvf-static-20261124f" not in _sw.text and "pvf-static-20261124g" not in _sw.text
          and "pvf-static-20261124h" not in _sw.text and "pvf-static-20261124i" not in _sw.text
          and "pvf-static-20261124j" not in _sw.text and "pvf-static-20261124k" not in _sw.text
          and "pvf-static-20261124l" not in _sw.text and "pvf-static-20261124m" not in _sw.text
          and "pvf-static-20261126a" not in _sw.text and "pvf-static-20261126b" not in _sw.text
          and "pvf-static-20261126c" not in _sw.text and "pvf-static-20261126d" not in _sw.text
          and "pvf-static-20261126e" not in _sw.text and "pvf-static-20261126f" in _sw.text)
    check("60. Todas las categorías visibles sin deslizar",
          ".cat-chips{display:flex;flex:1 1 100%;flex-wrap:wrap;gap:8px;overflow:visible" in _admin_css)
    check("61. Botones del encabezado sin deslizar en móvil",
          "body.pv-mobile .header-actions{width:100%;gap:6px;flex-wrap:wrap;overflow:visible" in _admin_css
          and "header-actions::-webkit-scrollbar" not in _admin_css)
    _cliente_sesion = TestClient(app)
    _entrada = _cliente_sesion.post("/api/admin/login",
                                    json={"email": ADMIN_EMAIL, "password": ADMIN_PASS})
    _login_con_sesion = _cliente_sesion.get("/admin/login", follow_redirects=False)
    check("62. El login del panel redirige al admin con sesión",
          _entrada.status_code == 200 and _login_con_sesion.status_code in (302, 307)
          and _login_con_sesion.headers.get("location", "").startswith("/admin"))
    _html_login = _anonimo_pwa.get("/admin/login").text
    check("63. La pantalla de acceso ofrece instalar el panel",
          'href="/admin.webmanifest"' in _html_login
          and 'id="admin-login-install-app"' in _html_login
          and 'src="/static/js/install.js"' in _html_login)
    check("64. Botones para eliminar visitas y borrar historial",
          'id="delete-visitas"' in _admin_html
          and 'data-delete-visit=' in _admin_js and 'data-clear-history=' in _admin_js
          and 'data-borrar-historial=' in _admin_js and '.btn-danger{' in _admin_css)

    # ---------------------------------------------------- pedidos y ventas
    pedido_producto = anonimo.post("/api/admin/productos", headers=admin_headers(), json={
        "category_id": cat_id, "name": "Producto para pedido", "description": "Prueba de pedidos",
        "price": 250, "image": "", "available": True, "is_demo": False, "sort_order": 98})
    check("72. Producto con precio para pedidos", pedido_producto.status_code == 200, pedido_producto.text)
    ppid = pedido_producto.json()["id"]

    reset_ventana()
    rped = registrar("Cliente Pedidos", "8092223333", "token-cliente-pedidos-12345")
    check("73. Cliente del pedido registrado", rped.status_code == 200, rped.text)
    cid_pedidos = db.one("SELECT id FROM customers WHERE phone='8092223333'")[0]
    actual = client.get("/api/cliente/actual").json()
    check("74. Nombre y teléfono vienen del servidor",
          actual["nombre"] == "Cliente Pedidos" and actual["telefono"] == "8092223333"
          and actual["cliente_id"] == cid_pedidos)

    token1 = "pedido-token-uno-123456"
    cuerpo1 = {
        "items": [{"product_id": ppid, "quantity": 2, "notes": "Sin cebolla"}],
        "request_token": token1, "delivery_method": "retiro",
        "notes": "Poco picante", "payment_method": "efectivo",
    }
    pedido1 = client.post("/api/pedido", json=cuerpo1)
    check("75. Confirmar pedido desde el menú",
          pedido1.status_code == 200 and pedido1.json()["duplicate"] is False, pedido1.text)
    num1 = pedido1.json()["numero"]
    check("76. Número único por pedido", re.match(r"^PV-\d{8}-\d{6}$", num1) is not None, num1)

    fila = db.one("SELECT * FROM orders WHERE order_number=?", (num1,))
    check("77. Pedido guardado con total del servidor",
          fila is not None and float(fila["subtotal"]) == 500.0 and float(fila["total"]) == 500.0
          and fila["status"] == "pendiente" and fila["customer_id"] == cid_pedidos)
    item_pedido = db.one("SELECT * FROM order_items WHERE order_id=?", (fila["id"],))
    check("78. Precio y nombre copiados en el detalle",
          item_pedido["product_name_snapshot"] == "Producto para pedido"
          and float(item_pedido["unit_price"]) == 250.0 and item_pedido["quantity"] == 2
          and item_pedido["notes"] == "Sin cebolla")
    pago1 = db.one("SELECT * FROM payments WHERE order_id=?", (fila["id"],))
    check("79. Pago pendiente hasta confirmación del admin",
          pago1["status"] == "pendiente" and pago1["method"] == "efectivo")

    duplicado = client.post("/api/pedido", json=cuerpo1)
    check("80. Doble clic no crea otro pedido",
          duplicado.status_code == 200 and duplicado.json()["duplicate"] is True
          and duplicado.json()["numero"] == num1
          and db.one("SELECT COUNT(*) FROM orders")[0] == 1, duplicado.text)

    mios = client.get("/api/pedidos/mios").json()["pedidos"]
    check("81. El cliente ve solamente su pedido", len(mios) == 1 and mios[0]["numero"] == num1)

    otro_cliente = TestClient(app)
    reset_ventana()
    r_otro = otro_cliente.post("/api/visita", json={
        "full_name": "Otro Cliente", "phone": "8094445555", "consent": True,
        "request_token": "token-otro-cliente-12345", "website": ""})
    check("82. Segundo cliente registrado", r_otro.status_code == 200, r_otro.text)
    check("83. Nadie puede consultar pedidos ajenos",
          otro_cliente.get("/api/pedidos/mios").json()["pedidos"] == [])

    listado_pedidos = anonimo.get("/api/admin/pedidos", headers=admin_headers()).json()
    check("84. Pedido visible en administración",
          listado_pedidos["total"] == 1 and listado_pedidos["pedidos"][0]["numero"] == num1)
    detalle = anonimo.get(f"/api/admin/pedidos/{fila['id']}", headers=admin_headers()).json()
    check("85. Detalle administrativo con artículos y pago",
          len(detalle["items"]) == 1 and detalle["pago"]["estado"] == "pendiente"
          and detalle["items"][0]["nombre"] == "Producto para pedido")

    estados = []
    for accion, esperado in (("aceptar", "aceptada"), ("preparando", "preparando"),
                             ("lista", "lista"), ("entregada", "entregada")):
        cambiar = anonimo.patch(f"/api/admin/pedidos/{fila['id']}/estado",
                                headers=admin_headers(), json={"accion": accion})
        estados.append(cambiar.status_code == 200 and cambiar.json()["estado"] == esperado)
    check("86. Aceptar, preparar, listo y entregado", all(estados), str(estados))

    sin_confirmar = anonimo.patch(f"/api/admin/pedidos/{fila['id']}/pago",
                                  headers=admin_headers(), json={"pagado": True})
    check("87. No se marca pagado sin confirmación válida", sin_confirmar.status_code == 400)
    pagado = anonimo.patch(f"/api/admin/pedidos/{fila['id']}/pago", headers=admin_headers(),
                           json={"pagado": True, "confirmado": True})
    check("88. Administrador confirma el pago",
          pagado.status_code == 200 and pagado.json()["pago"]["estado"] == "aprobado", pagado.text)
    admin_audit_id = db.one("SELECT id FROM admins WHERE email=?", (ADMIN_EMAIL,))[0]
    confirmacion = db.one("SELECT confirmed_by, confirmed_at FROM payments WHERE order_id=?",
                          (fila["id"],))
    check("88b. La confirmación registra quién y cuándo",
          confirmacion["confirmed_by"] == admin_audit_id and bool(confirmacion["confirmed_at"]),
          str(tuple(confirmacion)))
    doble_confirmacion = anonimo.patch(f"/api/admin/pedidos/{fila['id']}/pago",
                                       headers=admin_headers(),
                                       json={"pagado": True, "confirmado": True})
    check("88c. No se puede confirmar dos veces el mismo pago", doble_confirmacion.status_code == 400,
          doble_confirmacion.text)
    ventas1 = anonimo.get("/api/admin/ventas", headers=admin_headers()).json()
    check("89. Venta definitiva cuando se marca pagado",
          ventas1["total_ventas"] == 500.0 and ventas1["ventas_hoy"] == 500.0
          and ventas1["pagadas"] == 1)
    ventas2 = anonimo.get("/api/admin/ventas", headers=admin_headers()).json()
    check("90. El mismo pedido no se contabiliza dos veces", ventas2["total_ventas"] == 500.0)

    reset_ventana()
    registrar("Cliente Rechazo", "8093334444", "token-cliente-rechazo-12345")
    pedido2 = client.post("/api/pedido", json={
        "items": [{"product_id": ppid, "quantity": 1, "notes": ""}],
        "request_token": "pedido-token-dos-123456", "delivery_method": "retiro",
        "notes": "", "payment_method": "efectivo"})
    check("91. Segundo pedido registrado", pedido2.status_code == 200, pedido2.text)
    num2 = pedido2.json()["numero"]
    fila2 = db.one("SELECT * FROM orders WHERE order_number=?", (num2,))
    anonimo.patch(f"/api/admin/pedidos/{fila2['id']}/pago", headers=admin_headers(),
                  json={"pagado": True, "confirmado": True})
    check("92. Segunda venta antes del rechazo",
          anonimo.get("/api/admin/ventas", headers=admin_headers()).json()["total_ventas"] == 750.0)
    rechazo = anonimo.patch(f"/api/admin/pedidos/{fila2['id']}/estado",
                            headers=admin_headers(), json={"accion": "rechazar"})
    ventas3 = anonimo.get("/api/admin/ventas", headers=admin_headers()).json()
    check("93. Rechazado nunca suma y su pago se reembolsa",
          rechazo.status_code == 200 and ventas3["total_ventas"] == 500.0
          and db.one("SELECT status FROM payments WHERE order_id=?", (fila2["id"],))[0] == "reembolsado")

    perfil = anonimo.get(f"/api/admin/clientes/{cid_pedidos}/perfil", headers=admin_headers()).json()
    check("94. Perfil con visitas, pedidos, gasto y productos favoritos",
          perfil["total_pedidos"] == 1 and float(perfil["total_gastado"]) == 500.0
          and perfil["total_visitas"] >= 1 and perfil["favoritos"][0]["cantidad"] == 2)
    frecuentes = anonimo.get("/api/admin/clientes/frecuentes", headers=admin_headers()).json()["clientes"]
    check("95. Clientes frecuentes según compras pagadas",
          any(c["cliente_id"] == cid_pedidos and float(c["total"]) == 500.0 for c in frecuentes))

    pedidos_csv = anonimo.get("/api/admin/exportar/pedidos.csv", headers=admin_headers())
    ventas_csv = anonimo.get("/api/admin/exportar/ventas.csv", headers=admin_headers())
    texto_pedidos = pedidos_csv.content.decode("utf-8-sig")
    texto_ventas = ventas_csv.content.decode("utf-8-sig")
    check("96. Exportación CSV de pedidos y ventas",
          "N.º pedido" in texto_pedidos and num1 in texto_pedidos
          and num1 in texto_ventas and num2 not in texto_ventas)

    guarda_cliente = anonimo.delete(f"/api/admin/clientes/{cid_pedidos}", headers=admin_headers())
    guarda_producto = anonimo.delete(f"/api/admin/productos/{ppid}", headers=admin_headers())
    check("97. Cliente con pedidos no se puede eliminar", guarda_cliente.status_code == 409)
    check("98. Producto en pedidos no se puede eliminar", guarda_producto.status_code == 409)

    # ---------------------------------------------------- borrar una venta con contraseña
    check("113. Botón de borrar venta, modal y precio sobre los botones",
          '<th>Acciones</th>' in _admin_tpl
          and 'id="delete-venta-backdrop"' in _admin_tpl
          and 'id="delete-venta-password"' in _admin_tpl
          and 'data-borrar-venta=' in _admin_js
          and "`/api/admin/pedidos/${borrarVentaId}`" in _admin_js
          and 'data-borrar-pedido=' in _admin_js
          and 'data-borrar-comprobante=' in _admin_js
          and 'data-ver-comprobante=' in _admin_js
          and '📎 Ver comprobante' in _admin_js
          and 'id="comprobante-borrar"' in _admin_tpl
          and "`/api/admin/pedidos/${destinoId}/comprobante`" in _admin_js
          and 'colspan="6"' in _admin_js
          and re.search(r'product-desc">\$\{esc\(p\.description\)\}</p>\s*<span class="price ', _public_js) is not None
          and '.product-body > .price{' in _admin_css)

    historial_ventas = anonimo.get("/api/admin/ventas", headers=admin_headers()).json()["historial"]
    check("114. El historial de ventas incluye el id del pedido",
          any(v["numero"] == num1 and v["id"] == fila["id"] for v in historial_ventas),
          str(historial_ventas))

    sin_clave = anonimo.request("DELETE", f"/api/admin/pedidos/{fila['id']}", headers=admin_headers(),
                                json={"password": "ClaveIncorrecta123!"})
    check("115. Contraseña incorrecta no borra la venta",
          sin_clave.status_code == 400
          and db.one("SELECT COUNT(*) FROM orders WHERE id=?", (fila["id"],))[0] == 1
          and db.one("SELECT COUNT(*) FROM order_items WHERE order_id=?", (fila["id"],))[0] == 1
          and db.one("SELECT COUNT(*) FROM payments WHERE order_id=?", (fila["id"],))[0] == 1,
          sin_clave.text)

    borrar_venta = anonimo.request("DELETE", f"/api/admin/pedidos/{fila['id']}", headers=admin_headers(),
                                   json={"password": ADMIN_PASS})
    ventas_despues = anonimo.get("/api/admin/ventas", headers=admin_headers()).json()
    check("116. Contraseña correcta borra pedido, artículos y pago y recalcula ventas",
          borrar_venta.status_code == 200 and borrar_venta.json()["numero"] == num1
          and db.one("SELECT COUNT(*) FROM orders WHERE id=?", (fila["id"],))[0] == 0
          and db.one("SELECT COUNT(*) FROM order_items WHERE order_id=?", (fila["id"],))[0] == 0
          and db.one("SELECT COUNT(*) FROM payments WHERE order_id=?", (fila["id"],))[0] == 0
          and ventas_despues["total_ventas"] == 0.0 and ventas_despues["pagadas"] == 0,
          borrar_venta.text)

    repetir_borrado = anonimo.request("DELETE", f"/api/admin/pedidos/{fila['id']}", headers=admin_headers(),
                                      json={"password": ADMIN_PASS})
    check("117. Borrar dos veces da 404 y no toca otros pedidos",
          repetir_borrado.status_code == 404
          and db.one("SELECT COUNT(*) FROM orders WHERE id=?", (fila2["id"],))[0] == 1
          and db.one("SELECT status FROM payments WHERE order_id=?", (fila2["id"],))[0] == "reembolsado",
          repetir_borrado.text)

    # ------------------------------------------- transferencias y comprobantes
    _menu_transferencias = client.get("/menu").text
    _admin_transferencias = anonimo.get("/admin").text
    check("118. Sin tarjeta y con Mis pedidos y Pagos en la interfaz",
          '<option value="tarjeta">' not in _menu_transferencias
          and '"tarjeta"' not in _public_js
          and 'id="mis-pedidos-button"' in _menu_transferencias
          and 'id="checkout-bancos"' in _menu_transferencias
          and 'id="mis-pedidos-dialog"' in _menu_transferencias
          and 'data-section="pagos"' in _admin_transferencias
          and 'data-section="config-pagos"' in _admin_transferencias
          and 'id="comprobante-backdrop"' in _admin_transferencias)

    borradores = anonimo.get("/api/admin/pagos/cuentas", headers=admin_headers()).json()["cuentas"]
    check("119. Borradores bancarios sin titular ni cuenta inventados",
          [c["banco"] for c in borradores[:3]] == ["Banco Popular", "BHD", "Banreservas"]
          and all(not c["activa"] and not c["titular"] and not c["numero"]
                  for c in borradores[:3]), str(borradores[:3]))
    check("120. Los borradores no se publican al cliente",
          client.get("/api/pagos/cuentas").json()["cuentas"] == [])

    cliente_transferencia = TestClient(app)
    registro_trans = cliente_transferencia.post("/api/visita", json={
        "full_name": "Cliente Transferencia", "phone": "8094445555", "consent": True,
        "request_token": "visita-transferencia-123456", "website": "",
    })
    check("121. Cliente de transferencia registrado", registro_trans.status_code == 200,
          registro_trans.text)
    pedido_base = {
        "items": [{"product_id": ppid, "quantity": 1, "notes": ""}],
        "delivery_method": "retiro", "notes": "",
    }
    con_tarjeta = cliente_transferencia.post("/api/pedido", json={
        **pedido_base, "request_token": "pedido-tarjeta-rechazado-01",
        "payment_method": "tarjeta",
    })
    check("122. Tarjeta ya no se acepta como método de pago", con_tarjeta.status_code == 400,
          con_tarjeta.text)
    sin_banco = cliente_transferencia.post("/api/pedido", json={
        **pedido_base, "request_token": "pedido-sin-banco-12345",
        "payment_method": "transferencia",
    })
    check("123. Transferencia sin cuenta activa se rechaza", sin_banco.status_code == 400,
          sin_banco.text)

    cuenta_nueva = anonimo.post("/api/admin/pagos/cuentas", headers=admin_headers(), json={
        "banco": "Banco de prueba", "titular": "Titular de prueba", "numero": "0000000000",
        "tipo": "Ahorros", "identificacion": "000-0000000-0",
        "instrucciones": "Usa el número de pedido como concepto", "activa": True, "orden": 9,
    })
    check("124. Administrador crea una cuenta activa",
          cuenta_nueva.status_code == 200 and cuenta_nueva.json()["activa"] == 1,
          cuenta_nueva.text)
    cuenta_id = cuenta_nueva.json()["id"]
    publicadas = cliente_transferencia.get("/api/pagos/cuentas").json()["cuentas"]
    check("125. Solo la cuenta activa y completa llega al menú",
          len(publicadas) == 1 and publicadas[0]["id"] == cuenta_id
          and publicadas[0]["banco"] == "Banco de prueba", str(publicadas))

    # Si falla el envío de la notificación, el pedido debe quedar guardado igual.
    from punto_vivo import push as pv_push
    notificar_original = pv_push.notificar_nuevo_pedido
    pv_push.notificar_nuevo_pedido = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("fallo simulado de notificación"))
    try:
        pedido_trans = cliente_transferencia.post("/api/pedido", json={
            **pedido_base, "request_token": "pedido-transferencia-12345",
            "payment_method": "transferencia", "bank_account_id": cuenta_id,
        })
    finally:
        pv_push.notificar_nuevo_pedido = notificar_original
    check("126. Pedido guardado aunque falle la notificación",
          pedido_trans.status_code == 200 and pedido_trans.json()["pedido_id"] > 0,
          pedido_trans.text)
    tid = pedido_trans.json()["pedido_id"]
    fila_trans = db.one("SELECT bank_account_id FROM orders WHERE id=?", (tid,))
    pago_trans = db.one("SELECT method, status FROM payments WHERE order_id=?", (tid,))
    check("127. Transferencia pendiente y ligada a la cuenta elegida",
          fila_trans["bank_account_id"] == cuenta_id and pago_trans["method"] == "transferencia"
          and pago_trans["status"] == "pendiente", str(tuple(pago_trans)))

    mios = cliente_transferencia.get("/api/pedidos/mios").json()["pedidos"]
    check("128. Mis pedidos muestra el banco y el estado real del pago",
          any(p["id"] == tid and p["pago_estado"] == "pendiente"
              and p["banco"] == "Banco de prueba" for p in mios), str(mios))
    check("128b. Otro cliente no ve el pedido de transferencia",
          all(p["id"] != tid for p in client.get("/api/pedidos/mios").json()["pedidos"]))
    transferencias = anonimo.get("/api/admin/pagos/transferencias",
                                 headers=admin_headers()).json()
    check("128c. El panel lista las transferencias",
          any(p["id"] == tid for p in transferencias["pedidos"]))

    sin_comprobante_revision = anonimo.patch(f"/api/admin/pedidos/{tid}/revision",
                                             headers=admin_headers(),
                                             json={"decision": "aprobado", "nota": ""})
    check("129. No se aprueba una transferencia sin comprobante",
          sin_comprobante_revision.status_code == 400, sin_comprobante_revision.text)
    cliente_sin_admin = TestClient(app)
    sin_sesion_revision = cliente_sin_admin.patch(f"/api/admin/pedidos/{tid}/revision",
                                                  json={"decision": "aprobado", "nota": ""})
    check("129b. El cliente no puede revisar pagos", sin_sesion_revision.status_code == 401)

    archivo_falso = cliente_transferencia.post(f"/api/pedidos/{tid}/comprobante",
                                               files={"file": ("falso.txt", b"no", "text/plain")})
    check("130. Comprobante con extensión y cabecera falsas se rechaza",
          archivo_falso.status_code == 400, archivo_falso.text)
    sin_archivo = anonimo.get(f"/api/admin/pedidos/{tid}/comprobante", headers=admin_headers())
    check("131. Sin comprobante el admin recibe 404", sin_archivo.status_code == 404)
    sin_sesion_archivo = cliente_sin_admin.get(f"/api/admin/pedidos/{tid}/comprobante")
    check("131b. El comprobante exige sesión de administrador",
          sin_sesion_archivo.status_code == 401)

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 48
    subida = cliente_transferencia.post(f"/api/pedidos/{tid}/comprobante",
                                        files={"file": ("comprobante.png", png, "image/png")})
    check("132. Subir comprobante no confirma el pago",
          subida.status_code == 200 and subida.json()["pago_estado"] == "comprobante_recibido"
          and db.one("SELECT status FROM payments WHERE order_id=?", (tid,))[0] == "comprobante_recibido",
          subida.text)
    archivo = anonimo.get(f"/api/admin/pedidos/{tid}/comprobante", headers=admin_headers())
    detalle_admin = anonimo.get(f"/api/admin/pedidos/{tid}", headers=admin_headers())
    check("133. El admin puede ver el comprobante privado",
          archivo.status_code == 200
          and archivo.json()["datos"].startswith("data:image/png;base64,")
          and detalle_admin.status_code == 200
          and bool(detalle_admin.json().get("comprobante_enviado"))
          and detalle_admin.json().get("comprobante", {}).get("nombre") == "comprobante.png",
          archivo.text)

    # La URL firmada de Supabase viene como /object/sign/... y necesita /storage/v1.
    _enabled_original = storage.supabase_enabled
    _request_original = storage.supabase_request
    class _FirmaFalsa:
        def __init__(self, url): self._url = url
        def json(self): return {"signedURL": self._url}
    try:
        storage.supabase_enabled = lambda: True
        storage.supabase_request = lambda *a, **k: _FirmaFalsa(
            "/object/sign/comprobantes/prueba.png?token=abc")
        esperado = storage.SUPABASE_URL.rstrip('/') + "/storage/v1/object/sign/comprobantes/prueba.png?token=abc"
        check("133b. URL firmada relativa incluye /storage/v1",
              storage.signed_proof_url("comprobantes/prueba.png") == esperado)
        storage.supabase_request = lambda *a, **k: _FirmaFalsa(
            "/storage/v1/object/sign/comprobantes/prueba.png?token=abc")
        check("133c. URL firmada no duplica /storage/v1",
              storage.signed_proof_url("comprobantes/prueba.png") == esperado)
        storage.supabase_request = lambda *a, **k: _FirmaFalsa(
            "https://ejemplo.supabase.co/storage/v1/object/sign/x?token=abc")
        check("133d. URL firmada absoluta se respeta",
              storage.signed_proof_url("comprobantes/prueba.png") ==
              "https://ejemplo.supabase.co/storage/v1/object/sign/x?token=abc")
    finally:
        storage.supabase_enabled = _enabled_original
        storage.supabase_request = _request_original
    check("133e. El panel tiene respaldo base64 si falla la imagen",
          "imagen.addEventListener('error'" in _admin_js
          and "/comprobante?datos=1" in _admin_js
          and "if (resp.datos) imagen.src = resp.datos" in _admin_js)

    revision = anonimo.patch(f"/api/admin/pedidos/{tid}/revision", headers=admin_headers(),
                             json={"decision": "en_verificacion", "nota": "Revisando depósito"})
    auditado = db.one("SELECT reviewed_by, reviewed_at, review_note, confirmed_by "
                      "FROM payments WHERE order_id=?", (tid,))
    check("134. Revisión con nota queda auditada",
          revision.status_code == 200
          and revision.json()["pago"]["estado"] == "en_verificacion"
          and auditado["reviewed_by"] == admin_audit_id and bool(auditado["reviewed_at"])
          and auditado["review_note"] == "Revisando depósito"
          and auditado["confirmed_by"] is None, str(tuple(auditado)))
    nota_cliente = cliente_transferencia.get("/api/pedidos/mios").json()["pedidos"]
    check("135. La nota de la revisión llega al cliente",
          any(p["id"] == tid and p["pago_nota"] == "Revisando depósito" for p in nota_cliente))

    rechazo_comprobante = anonimo.patch(f"/api/admin/pedidos/{tid}/revision",
                                        headers=admin_headers(),
                                        json={"decision": "rechazado",
                                              "nota": "No apareció el depósito"})
    check("136. Rechazar comprobante no confirma la venta",
          rechazo_comprobante.status_code == 200
          and db.one("SELECT status FROM payments WHERE order_id=?", (tid,))[0] == "rechazado"
          and anonimo.get("/api/admin/ventas", headers=admin_headers()).json()["total_ventas"] == 0.0,
          rechazo_comprobante.text)
    reenvio = cliente_transferencia.post(f"/api/pedidos/{tid}/comprobante",
                                         files={"file": ("comprobante2.png", png, "image/png")})
    check("137. El cliente puede reenviar el comprobante tras un rechazo",
          reenvio.status_code == 200 and reenvio.json()["pago_estado"] == "comprobante_recibido",
          reenvio.text)

    aprobacion = anonimo.patch(f"/api/admin/pedidos/{tid}/revision", headers=admin_headers(),
                               json={"decision": "aprobado", "nota": "Transferencia recibida"})
    confirmado = db.one("SELECT status, confirmed_by, confirmed_at FROM payments WHERE order_id=?",
                        (tid,))
    check("138. El admin confirma tras verificar el dinero",
          aprobacion.status_code == 200 and confirmado["status"] == "aprobado"
          and confirmado["confirmed_by"] == admin_audit_id and bool(confirmado["confirmed_at"]),
          aprobacion.text)
    doble_revision = anonimo.patch(f"/api/admin/pedidos/{tid}/revision", headers=admin_headers(),
                                   json={"decision": "aprobado", "nota": "Otra vez"})
    check("139. La doble confirmación de transferencia se bloquea",
          doble_revision.status_code == 400, doble_revision.text)
    comprobante_tarde = cliente_transferencia.post(f"/api/pedidos/{tid}/comprobante",
                                                   files={"file": ("tarde.png", png, "image/png")})
    check("139b. Un pago aprobado ya no admite comprobantes",
          comprobante_tarde.status_code == 400, comprobante_tarde.text)
    check("140. La transferencia confirmada sí cuenta como venta",
          anonimo.get("/api/admin/ventas", headers=admin_headers()).json()["total_ventas"] == 250.0)

    ruta_prueba = db.one("SELECT file_path FROM payment_proofs WHERE order_id=?", (tid,))['file_path']
    clave_mala_proof = anonimo.request("DELETE", f"/api/admin/pedidos/{tid}/comprobante",
                                       headers=admin_headers(), json={"password": "ClaveIncorrecta123!"})
    check("140b. Contraseña incorrecta no borra el comprobante",
          clave_mala_proof.status_code == 400
          and db.one("SELECT COUNT(*) FROM payment_proofs WHERE order_id=?", (tid,))[0] == 1
          and Path(ruta_prueba).exists(), clave_mala_proof.text)
    sin_sesion_proof = cliente_sin_admin.request("DELETE", f"/api/admin/pedidos/{tid}/comprobante",
                                                 json={"password": ADMIN_PASS})
    check("140c. Borrar comprobante exige sesión de administrador",
          sin_sesion_proof.status_code == 401 and Path(ruta_prueba).exists(), sin_sesion_proof.text)
    borrar_proof = anonimo.request("DELETE", f"/api/admin/pedidos/{tid}/comprobante",
                                   headers=admin_headers(), json={"password": ADMIN_PASS})
    check("140d. Contraseña correcta borra archivo y registro del comprobante",
          borrar_proof.status_code == 200 and borrar_proof.json()["pedido_id"] == tid
          and db.one("SELECT COUNT(*) FROM payment_proofs WHERE order_id=?", (tid,))[0] == 0
          and not Path(ruta_prueba).exists(), borrar_proof.text)
    repetir_proof = anonimo.request("DELETE", f"/api/admin/pedidos/{tid}/comprobante",
                                    headers=admin_headers(), json={"password": ADMIN_PASS})
    check("140e. Borrar el comprobante dos veces da 404 y no altera la venta",
          repetir_proof.status_code == 404
          and anonimo.get("/api/admin/ventas", headers=admin_headers()).json()["total_ventas"] == 250.0,
          repetir_proof.text)
    fila_tid = db.one("SELECT customer_id, bank_account_id FROM orders WHERE id=?", (tid,))
    ruta_orden_borrada = storage.save_private_file(
        'comprobante-orden-borrada.png', png, 'image/png')
    repository.save_payment_proof(
        tid, fila_tid['customer_id'], fila_tid['bank_account_id'],
        'comprobante-orden-borrada.png', ruta_orden_borrada, 'image/png', len(png))
    borrar_con_comprobante = anonimo.request("DELETE", f"/api/admin/pedidos/{tid}",
                                             headers=admin_headers(),
                                             json={"password": ADMIN_PASS})
    check("140f. Borrar un pedido también borra su comprobante privado",
          borrar_con_comprobante.status_code == 200
          and db.one("SELECT COUNT(*) FROM orders WHERE id=?", (tid,))[0] == 0
          and db.one("SELECT COUNT(*) FROM payment_proofs WHERE order_id=?", (tid,))[0] == 0
          and not Path(ruta_orden_borrada).exists(), borrar_con_comprobante.text)

    clave_push = anonimo.get("/api/admin/push/clave", headers=admin_headers())
    check("141. Consulta de clave push sin exponer secretos",
          clave_push.status_code == 200 and isinstance(clave_push.json()["configurado"], bool)
          and "private" not in clave_push.text.lower()
          and "function sonarAlarma()" in _admin_js
          and "AudioContext" in _admin_js
          and "sonarAlarma();" in _admin_js
          and "sound: '/static/audio/alerta-fuerte.wav'" in _admin_js,
          clave_push.text)
    check("141b. Volumen configurable y sonido más fuerte",
          'id="volumen-estado"' in _admin_tpl and 'id="probar-sonido"' in _admin_tpl
          and 'data-volumen="0"' in _admin_tpl and 'data-volumen="1"' in _admin_tpl
          and ".volumen-btn.is-active" in _admin_css
          and "pv_volumen_alerta" in _admin_js
          and "function aplicarVolumenUI()" in _admin_js
          and "function desbloquearAudio()" in _admin_js
          and "guardado !== null" in _admin_js
          and "alerta-fuerte.wav" in _admin_js and "silent: false" in _admin_js
          and 'f"pvf-{numero}"' in (_base_ui / "push.py").read_text(encoding="utf-8")
          and (_base_ui / "static" / "audio" / "alerta-fuerte.wav").stat().st_size > 20000)
    suscripcion = {"endpoint": "https://ejemplo.invalid/push/prueba-unica",
                   "p256dh": "clave-publica-de-prueba", "auth": "secreto-de-prueba"}
    alta_push = anonimo.post("/api/admin/push/suscribir", headers=admin_headers(),
                             json=suscripcion)
    baja_push = anonimo.request("DELETE", "/api/admin/push/suscribir", headers=admin_headers(),
                                json=suscripcion)
    check("142. Suscripción push se registra y se retira",
          alta_push.status_code == 200 and baja_push.status_code == 200
          and db.one("SELECT COUNT(*) FROM admin_push_subscriptions WHERE endpoint=?",
                     (suscripcion["endpoint"],))[0] == 0, f"{alta_push.text} {baja_push.text}")
    check("143. Aviso de pedido abajo a la derecha en escritorio",
          'id="aviso-pedido"' in _admin_tpl and 'id="aviso-pedido-texto"' in _admin_tpl
          and 'id="aviso-ver"' in _admin_tpl and 'id="aviso-cerrar"' in _admin_tpl
          and ".aviso-pedido{position:fixed;right:24px;bottom:24px" in _admin_css
          and "body.pv-mobile .aviso-pedido{display:none}" in _admin_css
          and "function mostrarAvisoPedido(nuevos)" in _admin_js
          and "mostrarAvisoPedido(nuevos);" in _admin_js)
    prueba_push = anonimo.post("/api/admin/push/probar", headers=admin_headers())
    check("144. Notificación de prueba al centro de notificaciones",
          prueba_push.status_code == 200 and prueba_push.json().get('ok') is True
          and 'enviados' in prueba_push.json()
          and 'id="push-probar"' in _admin_tpl
          and "/api/admin/push/probar" in _admin_js
          and "def notificar_prueba()" in (_base_ui / "push.py").read_text(encoding="utf-8")
          and "«Enviar notificación de prueba»" in _admin_js,
          prueba_push.text)
    check("145. iPhone: alta push desde la app instalada y dentro del toque",
          "function esAppleMovil()" in _admin_js
          and "function panelInstalado()" in _admin_js
          and "navigator.standalone === true" in _admin_js
          and "const apple = esAppleMovil();" in _admin_js
          and "if (apple && !instalada)" in _admin_js
          and "botonActivar.disabled = true" in _admin_js
          and "if (!clavePush) clavePush = await api('/api/admin/push/clave');" in _admin_js
          and "registroPush.pushManager.subscribe({" in _admin_js
          and "Notification.requestPermission()" not in _admin_js
          and "Ajustes → Notificaciones → Panel PVF" in _admin_js
          and "Abrir como app web" in _install_js)
    estado_push = anonimo.get("/api/admin/push/estado", headers=admin_headers())
    check("146. Diagnóstico push sin exponer secretos",
          estado_push.status_code == 200
          and {'configurado', 'suscripciones', 'destinos'} <= set(estado_push.json())
          and 'id="push-diagnostico"' in _admin_tpl
          and "/api/admin/push/estado" in _admin_js
          and "Última prueba:" in _admin_js
          and "def estado()" in (_base_ui / "push.py").read_text(encoding="utf-8")
          and "endpoint" not in estado_push.text.lower(), estado_push.text)
    check("147. Prueba en segundo plano al minimizar",
          isinstance(estado_push.json().get('claves_coinciden'), bool)
          and isinstance(estado_push.json().get('subject_valido'), bool)
          and 'id="push-probar-fondo"' in _admin_tpl
          and "let pruebaAlMinimizar = false;" in _admin_js
          and "document.addEventListener('visibilitychange'" in _admin_js
          and "method: 'POST', keepalive: true" in _admin_js
          and "par ${servidor.claves_coinciden ? 'correcto' : 'INCORRECTO'}" in _admin_js
          and "ttl=3600" in (_base_ui / "push.py").read_text(encoding="utf-8")
          and 'headers={"Urgency": "high"}' in (_base_ui / "push.py").read_text(encoding="utf-8"))

    _menu_pedidos = client.get("/menu").text
    _public_js_pedidos = (_base_ui / "static" / "js" / "public.js").read_text(encoding="utf-8")
    check("99. Carrito y checkout presentes en el menú",
          'id="cart-button"' in _menu_pedidos and 'id="cart-dialog"' in _menu_pedidos
          and 'id="checkout-dialog"' in _menu_pedidos and 'data-add=' in _public_js_pedidos
          and "/api/pedido" in _public_js_pedidos
          and 'id="success-proof-file"' in _menu_pedidos
          and 'id="success-close"' in _menu_pedidos
          and "success-close').addEventListener('click', async" in _public_js_pedidos
          and "subirComprobante(ultimoPedido.pedido_id, archivo, estadoEl)" in _public_js_pedidos
          and "y pulsa Listo para enviarlo" in _menu_pedidos)
    check("99b. Ficha centrada y foto ampliable al tocarla",
          "function detailImageHtml(item)" in _public_js_pedidos
          and "${detailImageHtml(p)}" in _public_js_pedidos
          and 'class="product-detail-img"><img src=' in _public_js_pedidos
          and ".product-detail-img img{display:block;width:auto;height:auto;max-width:100%;max-height:min(40vh,240px);object-fit:contain" in _admin_css
          and "body.pv-mobile .product-detail-img img{width:auto;height:auto;max-width:100%;max-height:min(28dvh,180px);object-fit:contain" in _admin_css
          and "dialog.dialog-product,dialog.dialog-image{position:fixed;top:50%;left:50%;bottom:auto;right:auto;transform:translate(-50%,-50%);margin:0}" in _admin_css
          and "body.pv-mobile dialog.dialog-product{width:min(92vw,430px);max-width:min(92vw,430px);max-height:82dvh;border-radius:18px;overflow:auto}" in _admin_css
          and "dialog.dialog-product{width:min(92vw,430px);max-width:min(92vw,430px);max-height:82dvh;border-radius:18px;overflow:auto}" in _admin_css
          and 'id="image-dialog"' in _menu_pedidos
          and 'id="image-zoom"' in _menu_pedidos
          and "close-image').addEventListener('click'" in _public_js_pedidos
          and ".product-detail-img img')?.addEventListener('click'" in _public_js_pedidos
          and "loading=\"lazy\"" not in _public_js_pedidos.split("function detailImageHtml")[1].split("function render")[0])
    check("100. Secciones PEDIDOS, VENTAS y CLIENTES FRECUENTES",
          'data-section="pedidos"' in _admin_tpl and 'data-section="ventas"' in _admin_tpl
          and 'data-section="frecuentes"' in _admin_tpl
          and 'id="profile-backdrop"' in anonimo.get("/admin").text)

    # Eliminaciones: se ejecutan al final para no afectar al resto de comprobaciones
    registrar("María Borrar", "8091112222", "token-borrar-historial-123")
    cid_maria = db.one("SELECT id FROM customers WHERE phone='8091112222'")[0]
    borrar_hist = anonimo.delete(f"/api/admin/clientes/{cid_maria}/visitas", headers=admin_headers())
    check("65. Borrar el historial de un cliente conserva el cliente",
          borrar_hist.status_code == 200 and borrar_hist.json()["eliminadas"] >= 1
          and db.one("SELECT COUNT(*) FROM visits WHERE customer_id=?", (cid_maria,))[0] == 0
          and db.one("SELECT COUNT(*) FROM customers WHERE id=?", (cid_maria,))[0] == 1)

    reset_ventana()
    registrar("Visita Suelta", "8095552222", "token-borrar-una-123456789")
    vid_suelta = db.one("SELECT id FROM visits WHERE phone='8095552222'")[0]
    borrar_una = anonimo.delete(f"/api/admin/visitas/{vid_suelta}", headers=admin_headers())
    no_existe = anonimo.delete(f"/api/admin/visitas/{vid_suelta}", headers=admin_headers())
    check("66. Eliminar una visita concreta",
          borrar_una.status_code == 200 and borrar_una.json()["eliminadas"] == 1
          and no_existe.status_code == 404
          and db.one("SELECT COUNT(*) FROM visits WHERE id=?", (vid_suelta,))[0] == 0)

    reset_ventana()
    registrar("Borrar Filtrado", "8097778888", "token-borrar-filtro-12345678")
    total_antes_filtro = db.one("SELECT COUNT(*) FROM visits")[0]
    borrar_filtro = anonimo.delete("/api/admin/visitas?q=Borrar%20Filtrado", headers=admin_headers())
    check("67. Eliminar solo las visitas del filtro",
          borrar_filtro.status_code == 200 and borrar_filtro.json()["eliminadas"] == 1
          and db.one("SELECT COUNT(*) FROM visits")[0] == total_antes_filtro - 1
          and db.one("SELECT COUNT(*) FROM visits WHERE full_name LIKE '%Juan%'")[0] >= 1,
          f"status={borrar_filtro.status_code} body={borrar_filtro.text} antes={total_antes_filtro} "
          f"ahora={db.one('SELECT COUNT(*) FROM visits')[0]}")

    borrar_todas = anonimo.delete("/api/admin/visitas", headers=admin_headers())
    resumen_vacio = anonimo.get("/api/admin/resumen", headers=admin_headers()).json()
    check("68. Eliminar todas las visitas actualiza el resumen",
          borrar_todas.status_code == 200 and borrar_todas.json()["eliminadas"] >= 1
          and db.one("SELECT COUNT(*) FROM visits")[0] == 0
          and resumen_vacio["total_visitas"] == 0)
    check("69. Guía visible al pulsar Instalar en iPhone",
          "pv-install-guide" in _install_js
          and "esAppleMovil()" in _install_js
          and "Añadir a pantalla de inicio" in _install_js
          and "beforeinstallprompt" in _install_js
          and ".pv-guide-backdrop{" in _admin_css
          and "pvf-static-20261126f" in _sw.text)
    check("70. Botones para eliminar clientes",
          "data-delete-customer=" in _admin_js
          and "data-borrar-cliente=" in _admin_js
          and "history-actions" in _admin_css)

    reset_ventana()
    registrar("Cliente A Borrar", "8099998888", "token-eliminar-cliente-1234")
    cid_borrar = db.one("SELECT id FROM customers WHERE phone='8099998888'")[0]
    otros_clientes = db.one("SELECT COUNT(*) FROM customers")[0]
    borrar_cliente = anonimo.delete(f"/api/admin/clientes/{cid_borrar}", headers=admin_headers())
    no_existe_cliente = anonimo.delete(f"/api/admin/clientes/{cid_borrar}", headers=admin_headers())
    check("71. Eliminar cliente borra su historial y conserva los demás",
          borrar_cliente.status_code == 200
          and borrar_cliente.json()["eliminado"] == 1
          and borrar_cliente.json()["visitas"] >= 1
          and no_existe_cliente.status_code == 404
          and db.one("SELECT COUNT(*) FROM customers WHERE id=?", (cid_borrar,))[0] == 0
          and db.one("SELECT COUNT(*) FROM visits WHERE customer_id=?", (cid_borrar,))[0] == 0
          and db.one("SELECT COUNT(*) FROM customers")[0] == otros_clientes - 1,
          f"status={borrar_cliente.status_code} body={borrar_cliente.text}")

    # Cerrar sesión del cliente: se ejecuta al final para no afectar al resto
    check("72. Botón de cerrar sesión y limpieza del carrito",
          'id="cerrar-sesion"' in _menu_tpl
          and "'/api/visita/salir'" in _public_js
          and "localStorage.removeItem(clave)" in _public_js
          and "sessionStorage.removeItem(clave)" in _public_js)
    reset_ventana()
    cliente_salida = TestClient(app)
    registro_salida = cliente_salida.post("/api/visita", json={
        "full_name": "Cliente Salida", "phone": "8291113333", "consent": True,
        "request_token": "token-cerrar-sesion-1234", "website": "",
    })
    dentro = cliente_salida.get("/menu")
    salida = cliente_salida.post("/api/visita/salir")
    fuera = cliente_salida.get("/menu", follow_redirects=False)
    check("73. El cliente cierra sesión y vuelve al registro",
          registro_salida.status_code == 200 and dentro.status_code == 200
          and salida.status_code == 200 and salida.json()["ok"] is True
          and salida.json()["redirect"] == "/bienvenida"
          and "pv_visita=" in salida.headers.get("set-cookie", "")
          and "Max-Age=0" in salida.headers.get("set-cookie", "")
          and fuera.status_code == 302 and "/bienvenida" in fuera.headers["location"],
          f"status={salida.status_code} body={salida.text}")
    externo = cliente_salida.post("/api/visita/salir",
                                  headers={"Origin": "https://sitio-malicioso.invalid"})
    check("74. Un origen externo no puede cerrar la sesión del cliente",
          externo.status_code == 403, externo.text)

    # Regresión Vercel: el arranque no debe crear carpetas en disco de solo lectura
    from punto_vivo import config as pv_config
    _serverless_antes = pv_config.IS_SERVERLESS
    _db_antes = pv_config.DB_PATH
    _ruta_serverless = Path(TMP) / "no-debe-crearse" / "db.sqlite3"
    try:
        pv_config.IS_SERVERLESS = True
        pv_config.DB_PATH = _ruta_serverless
        pv_config.ensure_dirs()
        check("34. ensure_dirs omite mkdir en serverless",
              not _ruta_serverless.parent.exists())
    finally:
        pv_config.IS_SERVERLESS = _serverless_antes
        pv_config.DB_PATH = _db_antes

    print("-" * 52)
    print(f"RESULTADO: {passed} pruebas superadas.\n")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as exc:
        print(f"\n{exc}")
        sys.exit(1)
