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

from punto_vivo import admin, db  # noqa: E402
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
