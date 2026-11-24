"""Acceso a datos de PUNTO VIVO FRESH con dos backends intercambiables.

- **Local:** SQLite (``db.py``), exactamente el comportamiento de siempre.
- **Producción:** Supabase/Postgres vía REST + RPC, usando la SERVICE_ROLE_KEY
  solo en el servidor. Se activa al definir PV_SUPABASE_URL y
  PV_SUPABASE_SERVICE_ROLE_KEY.

Ninguna ruta HTTP, validación ni regla de negocio cambia entre backends.
"""
from __future__ import annotations

import re
import time
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import httpx

from . import db
from .config import (
    MAX_VISITAS_HORA_IP,
    MAX_VISITAS_HORA_TEL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    now_local,
    supabase_enabled,
)
from .db import DEMO_CATEGORIES, DEMO_DESCRIPTION, DEMO_PRODUCTS

_client: httpx.Client | None = None


# ------------------------------------------------------------------ cliente --

def _client_supabase() -> httpx.Client:
    global _client
    if _client is None:
        if not supabase_enabled():
            raise RuntimeError("Supabase no está configurado.")
        _client = httpx.Client(
            base_url=SUPABASE_URL,
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
            },
            timeout=20.0,
        )
    return _client


def supabase_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Petición autenticada a Supabase. Se usa también desde storage.py."""
    response = _client_supabase().request(method, path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f"Supabase {method} {path}: {response.status_code} {response.text[:300]}")
    return response


def _rows(table: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    json_rows = supabase_request("GET", f"/rest/v1/{table}", params=params or {}).json()
    return [_normalize(r) for r in json_rows]


def _row(table: str, params: dict[str, Any]) -> dict[str, Any] | None:
    found = _rows(table, {**params, "limit": 1})
    return found[0] if found else None


def _insert(table: str, payload: dict[str, Any]) -> dict[str, Any]:
    res = supabase_request(
        "POST", f"/rest/v1/{table}", json=payload,
        headers={"Prefer": "return=representation"},
    ).json()
    return _normalize(res[0]) if res else {}


def _update(table: str, row_id: Any, payload: dict[str, Any]) -> None:
    supabase_request(
        "PATCH", f"/rest/v1/{table}", params={"id": f"eq.{row_id}"}, json=payload,
        headers={"Prefer": "return=minimal"},
    )


def _delete(table: str, row_id: Any) -> None:
    supabase_request(
        "DELETE", f"/rest/v1/{table}", params={"id": f"eq.{row_id}"},
        headers={"Prefer": "return=minimal"},
    )


def _delete_where(table: str, column: str, value: Any) -> None:
    supabase_request(
        "DELETE", f"/rest/v1/{table}", params={column: f"eq.{value}"},
        headers={"Prefer": "return=minimal"},
    )


def _rpc(name: str, payload: dict[str, Any]) -> Any:
    return supabase_request("POST", f"/rest/v1/rpc/{name}", json=payload).json()


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    """Booleans de Postgres → ints, para que la app vea los mismos tipos."""
    for key in ("active", "available", "is_demo"):
        if key in row and isinstance(row[key], bool):
            row[key] = int(row[key])
    return row


def _local_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = db.one(sql, params)
    return dict(row) if row is not None else None


def _local_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in db.all_rows(sql, params)]


def _scalar(sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = db.one(sql, params)
    return row[0] if row is not None else 0


def _like(value: str) -> str:
    return "%" + value.strip().replace("%", "\\%").replace("_", "\\_") + "%"


# ------------------------------------------------------------------ esquema --

def init_db() -> None:
    if not supabase_enabled():
        db.init_db()
        return

    # En Vercel varias funciones frías pueden arrancar a la vez: pv_meta
    # garantiza que solo una siembre las categorías y productos de demostración.
    claimed = supabase_request(
        "POST", "/rest/v1/pv_meta",
        json={"key": "demo_seeded"},
        headers={"Prefer": "return=representation,resolution=ignore-duplicates"},
    ).json()
    if not claimed:
        return
    try:
        category_ids: dict[str, Any] = {}
        for name, description, order in DEMO_CATEGORIES:
            category_ids[name] = _insert("categories", {
                "name": name, "description": description,
                "sort_order": order, "active": True,
            })["id"]
        now = now_local().isoformat(timespec="seconds")
        for category_name, items in DEMO_PRODUCTS.items():
            for order, (name, image) in enumerate(items):
                _insert("products", {
                    "category_id": category_ids[category_name],
                    "name": name,
                    "description": DEMO_DESCRIPTION,
                    "price": 0,
                    "image": image,
                    "available": True,
                    "is_demo": True,
                    "sort_order": order,
                    "created_at": now,
                    "updated_at": now,
                })
    except Exception:
        supabase_request(
            "DELETE", "/rest/v1/pv_meta", params={"key": "eq.demo_seeded"}
        )
        raise


# ------------------------------------------------------------------- menú ----

def menu_categories() -> list[dict[str, Any]]:
    if supabase_enabled():
        return _rows("categories", {
            "select": "id,name,description,image", "active": "eq.true",
            "order": "sort_order.asc,id.asc",
        })
    return _local_all(
        "SELECT id, name, description, image FROM categories WHERE active = 1 ORDER BY sort_order, id"
    )


def menu_products() -> list[dict[str, Any]]:
    if supabase_enabled():
        categories = _rows("categories", {
            "select": "id,name,sort_order", "active": "eq.true", "order": "sort_order.asc,id.asc",
        })
        order = {str(c["id"]): (c["sort_order"], c["id"]) for c in categories}
        names = {str(c["id"]): c["name"] for c in categories}
        products = _rows("products", {
            "select": "id,category_id,name,description,price,image,available,is_demo,sort_order",
            "available": "eq.true",
        })
        products = [p for p in products if str(p["category_id"]) in order]
        products.sort(key=lambda p: (
            order[str(p["category_id"])][0],
            order[str(p["category_id"])][1],
            p["sort_order"], p["id"],
        ))
        for p in products:
            p["categoria"] = names[str(p["category_id"])]
        return products
    return _local_all(
        """SELECT p.id, p.category_id, p.name, p.description, p.price, p.image,
                  p.available, p.is_demo, p.sort_order, c.name AS categoria
           FROM products p JOIN categories c ON c.id = p.category_id
           WHERE p.available = 1 AND c.active = 1
           ORDER BY c.sort_order, c.id, p.sort_order, p.id"""
    )


def menu_product(product_id: int) -> dict[str, Any] | None:
    if supabase_enabled():
        product = _row("products", {"id": f"eq.{product_id}", "available": "eq.true"})
        if product is None:
            return None
        category = _row("categories", {"id": f"eq.{product['category_id']}", "active": "eq.true"})
        if category is None:
            return None
        product["categoria"] = category["name"]
        return product
    row = _local_one(
        """SELECT p.*, c.name AS categoria FROM products p
           JOIN categories c ON c.id = p.category_id
           WHERE p.id = ? AND p.available = 1 AND c.active = 1""",
        (product_id,),
    )
    return row


# ------------------------------------------------------------- categorías ----

def active_category(category_id: int) -> dict[str, Any] | None:
    if supabase_enabled():
        return _row("categories", {"id": f"eq.{category_id}", "active": "eq.true", "select": "id"})
    return _local_one("SELECT id FROM categories WHERE id = ? AND active = 1", (category_id,))


def get_category(category_id: int) -> dict[str, Any] | None:
    if supabase_enabled():
        return _row("categories", {"id": f"eq.{category_id}"})
    return _local_one("SELECT id FROM categories WHERE id = ?", (category_id,))


def get_category_by_name(name: str) -> dict[str, Any] | None:
    if supabase_enabled():
        return _row("categories", {"name": f"eq.{name}", "select": "id"})
    return _local_one("SELECT id FROM categories WHERE name = ?", (name,))


def admin_categories() -> list[dict[str, Any]]:
    if supabase_enabled():
        categories = _rows("categories", {"order": "sort_order.asc,id.asc"})
        products = _rows("products", {"select": "category_id"})
        counts: dict[str, int] = {}
        for p in products:
            key = str(p["category_id"])
            counts[key] = counts.get(key, 0) + 1
        for c in categories:
            c["productos"] = counts.get(str(c["id"]), 0)
        return categories
    rows = _local_all("SELECT * FROM categories ORDER BY sort_order, id")
    for row in rows:
        row["productos"] = _scalar(
            "SELECT COUNT(*) FROM products WHERE category_id = ?", (row["id"],)
        )
    return rows


def create_category(name: str, description: str, image: str,
                    sort_order: int, active: bool) -> int:
    if supabase_enabled():
        return _insert("categories", {
            "name": name, "description": description, "image": image,
            "sort_order": sort_order, "active": active,
        })["id"]
    return db.execute(
        "INSERT INTO categories(name, description, image, sort_order, active) VALUES(?,?,?,?,?)",
        (name, description, image, sort_order, int(active)),
    )


def update_category(category_id: int, name: str, description: str, image: str,
                    sort_order: int, active: bool) -> None:
    if supabase_enabled():
        _update("categories", category_id, {
            "name": name, "description": description, "image": image,
            "sort_order": sort_order, "active": active,
        })
    else:
        db.execute(
            "UPDATE categories SET name=?, description=?, image=?, sort_order=?, active=? WHERE id=?",
            (name, description, image, sort_order, int(active), category_id),
        )


def delete_category(category_id: int) -> None:
    if supabase_enabled():
        _delete("categories", category_id)
    else:
        db.execute("DELETE FROM categories WHERE id = ?", (category_id,))


def count_products_in_category(category_id: int) -> int:
    if supabase_enabled():
        return len(_rows("products", {"select": "id", "category_id": f"eq.{category_id}"}))
    return int(_scalar("SELECT COUNT(*) FROM products WHERE category_id = ?", (category_id,)))


# ---------------------------------------------------------------- productos --

def admin_products() -> list[dict[str, Any]]:
    if supabase_enabled():
        products = _rows("products", {})
        names = {str(c["id"]): c["name"] for c in _rows("categories", {"select": "id,name"})}
        for p in products:
            p["categoria"] = names.get(str(p["category_id"]), "")
        return products
    return _local_all(
        """SELECT p.*, c.name AS categoria FROM products p
           JOIN categories c ON c.id = p.category_id ORDER BY c.sort_order, p.sort_order, p.id"""
    )


def get_product(product_id: int) -> dict[str, Any] | None:
    if supabase_enabled():
        return _row("products", {"id": f"eq.{product_id}", "select": "id"})
    return _local_one("SELECT id FROM products WHERE id = ?", (product_id,))


def create_product(payload: dict[str, Any]) -> int:
    if supabase_enabled():
        return _insert("products", payload)["id"]
    return db.execute(
        """INSERT INTO products(category_id, name, description, price, image, available,
                                is_demo, sort_order, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            payload["category_id"], payload["name"], payload["description"],
            payload["price"], payload["image"], int(payload["available"]),
            int(payload["is_demo"]), payload["sort_order"],
            payload["created_at"], payload["updated_at"],
        ),
    )


def update_product(product_id: int, payload: dict[str, Any]) -> None:
    if supabase_enabled():
        _update("products", product_id, payload)
    else:
        db.execute(
            """UPDATE products SET category_id=?, name=?, description=?, price=?, image=?,
                                   available=?, is_demo=?, sort_order=?, updated_at=? WHERE id=?""",
            (
                payload["category_id"], payload["name"], payload["description"],
                payload["price"], payload["image"], int(payload["available"]),
                int(payload["is_demo"]), payload["sort_order"], payload["updated_at"],
                product_id,
            ),
        )


def delete_product(product_id: int) -> None:
    if count_product_orders(product_id):
        raise ValueError('No se puede borrar: el producto aparece en pedidos anteriores.')
    if supabase_enabled():
        _delete("products", product_id)
    else:
        db.execute("DELETE FROM products WHERE id = ?", (product_id,))


def set_product_available(product_id: int, available: bool, updated_at: str) -> None:
    if supabase_enabled():
        _update("products", product_id, {"available": available, "updated_at": updated_at})
    else:
        db.execute(
            "UPDATE products SET available=?, updated_at=? WHERE id=?",
            (int(available), updated_at, product_id),
        )


def set_product_sort_order(product_id: int, order: int) -> None:
    if supabase_enabled():
        _update("products", product_id, {"sort_order": order})
    else:
        db.execute("UPDATE products SET sort_order=? WHERE id=?", (order, product_id))


def set_product_image(product_id: int, url: str, updated_at: str) -> None:
    if supabase_enabled():
        _update("products", product_id, {"image": url, "updated_at": updated_at})
    else:
        db.execute(
            "UPDATE products SET image=?, updated_at=? WHERE id=?",
            (url, updated_at, product_id),
        )


# ------------------------------------------------------ admins y sesiones ----

def get_admin_by_email(email: str) -> dict[str, Any] | None:
    if supabase_enabled():
        return _row("admins", {"email": f"eq.{email}"})
    return _local_one("SELECT * FROM admins WHERE email = ?", (email,))


def list_admins() -> list[dict[str, Any]]:
    if supabase_enabled():
        return _rows("admins", {"select": "id,email,role,created_at", "order": "id.asc"})
    return _local_all("SELECT id, email, role, created_at FROM admins ORDER BY id")


def get_admin_by_id(admin_id: Any) -> dict[str, Any] | None:
    if supabase_enabled():
        return _row("admins", {"id": f"eq.{admin_id}", "select": "id,email,role,created_at"})
    return _local_one("SELECT id, email, role, created_at FROM admins WHERE id = ?", (admin_id,))


def update_admin_email(admin_id: Any, email: str) -> None:
    if supabase_enabled():
        _update("admins", admin_id, {"email": email})
    else:
        db.execute("UPDATE admins SET email = ? WHERE id = ?", (email, admin_id))


def create_admin(email: str, password_hash: str, role: str, created_at: str) -> int:
    if supabase_enabled():
        return _insert("admins", {
            "email": email, "password_hash": password_hash,
            "role": role, "created_at": created_at,
        })["id"]
    return db.execute(
        "INSERT INTO admins(email, password_hash, role, created_at) VALUES(?,?,?,?)",
        (email, password_hash, role, created_at),
    )


def change_admin_password(admin_id: Any, password_hash: str) -> None:
    if supabase_enabled():
        _update("admins", admin_id, {"password_hash": password_hash})
    else:
        db.execute("UPDATE admins SET password_hash = ? WHERE id = ?", (password_hash, admin_id))


def delete_sessions_by_admin(admin_id: Any) -> None:
    if supabase_enabled():
        _delete_where("admin_sessions", "admin_id", admin_id)
    else:
        db.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (admin_id,))


def create_session(token_hash: str, admin_id: Any, csrf: str,
                   created_at: float, expires_at: float) -> None:
    payload = {
        "token_hash": token_hash, "admin_id": admin_id, "csrf_token": csrf,
        "created_at": created_at, "expires_at": expires_at,
    }
    if supabase_enabled():
        _insert("admin_sessions", payload)
    else:
        db.execute(
            "INSERT INTO admin_sessions(token_hash, admin_id, csrf_token, created_at, expires_at) "
            "VALUES(?,?,?,?,?)",
            (token_hash, admin_id, csrf, created_at, expires_at),
        )


def get_session_by_hash(token_hash: str) -> dict[str, Any] | None:
    if supabase_enabled():
        session = _row("admin_sessions", {"token_hash": f"eq.{token_hash}"})
        if session is None:
            return None
        admin = _row("admins", {"id": f"eq.{session['admin_id']}"})
        if admin is None:
            return None
        return {**session, "email": admin["email"], "role": admin["role"]}
    return _local_one(
        "SELECT s.*, a.email, a.role FROM admin_sessions s "
        "JOIN admins a ON a.id = s.admin_id WHERE s.token_hash = ?",
        (token_hash,),
    )


def delete_session_by_hash(token_hash: str) -> None:
    if supabase_enabled():
        _delete_where("admin_sessions", "token_hash", token_hash)
    else:
        db.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (token_hash,))


# ----------------------------------------------------------- anti-abuso ------

def allow_visit(client_hash: str, phone_hash: str) -> bool:
    """Ventana deslizante de 1 h por IP y por teléfono (una transacción)."""
    now = time.time()
    cutoff = now - 3600
    if supabase_enabled():
        ip_ok = _rpc("pv_allow_event", {
            "p_scope": "visita_ip", "p_key_hash": client_hash,
            "p_created_epoch": now, "p_max": MAX_VISITAS_HORA_IP, "p_cutoff": cutoff,
        })
        if not ip_ok:
            return False
        return bool(_rpc("pv_allow_event", {
            "p_scope": "visita_tel", "p_key_hash": phone_hash,
            "p_created_epoch": now, "p_max": MAX_VISITAS_HORA_TEL, "p_cutoff": cutoff,
        }))
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rate_events WHERE created_epoch < ?", (cutoff - 3600,))
        by_ip = conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE scope='visita_ip' AND key_hash=? AND created_epoch>?",
            (client_hash, cutoff),
        ).fetchone()[0]
        by_phone = conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE scope='visita_tel' AND key_hash=? AND created_epoch>?",
            (phone_hash, cutoff),
        ).fetchone()[0]
        if by_ip >= MAX_VISITAS_HORA_IP or by_phone >= MAX_VISITAS_HORA_TEL:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO rate_events(scope, key_hash, created_epoch) VALUES('visita_ip',?,?)",
            (client_hash, now),
        )
        conn.execute(
            "INSERT INTO rate_events(scope, key_hash, created_epoch) VALUES('visita_tel',?,?)",
            (phone_hash, now),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


LOGIN_MAX_INTENTOS = 10
LOGIN_VENTANA_SEG = 3600


def login_attempts(client_hash: str) -> int:
    """Solo consulta: cuenta los fallos recientes sin consumir intento."""
    cutoff = time.time() - LOGIN_VENTANA_SEG
    if supabase_enabled():
        return len(_rows("rate_events", {
            "scope": "eq.login_ip",
            "key_hash": f"eq.{client_hash}",
            "created_epoch": "gt.%.3f" % cutoff,
            "select": "id",
        }))
    conn = db.connect()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE scope='login_ip' AND key_hash=? AND created_epoch>?",
            (client_hash, cutoff),
        ).fetchone()[0]
    finally:
        conn.close()


def register_login_failure(client_hash: str) -> None:
    """Registra únicamente un intento fallido (el RPC ya aplica el tope)."""
    now = time.time()
    cutoff = now - LOGIN_VENTANA_SEG
    if supabase_enabled():
        _rpc("pv_allow_event", {
            "p_scope": "login_ip", "p_key_hash": client_hash,
            "p_created_epoch": now, "p_max": LOGIN_MAX_INTENTOS, "p_cutoff": cutoff,
        })
        return
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rate_events WHERE created_epoch < ?", (cutoff - LOGIN_VENTANA_SEG,))
        attempts = conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE scope='login_ip' AND key_hash=? AND created_epoch>?",
            (client_hash, cutoff),
        ).fetchone()[0]
        if attempts < LOGIN_MAX_INTENTOS:
            conn.execute(
                "INSERT INTO rate_events(scope, key_hash, created_epoch) VALUES('login_ip',?,?)",
                (client_hash, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def clear_login_attempts(client_hash: str) -> None:
    """Un login correcto deja sin efecto los fallos anteriores de esa IP."""
    if supabase_enabled():
        supabase_request("DELETE", "/rest/v1/rate_events", params={
            "scope": "eq.login_ip", "key_hash": f"eq.{client_hash}",
        })
        return
    conn = db.connect()
    try:
        conn.execute("DELETE FROM rate_events WHERE scope='login_ip' AND key_hash=?", (client_hash,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- visitas ---

def register_visit(full_name: str, phone: str, request_token: str,
                   client_hash: str, epoch: float, created_at: str) -> dict[str, Any]:
    """Cliente + visita en una transacción; idempotente por request_token."""
    if supabase_enabled():
        result = _rpc("registrar_visita", {
            "p_nombre": full_name, "p_telefono": phone, "p_token": request_token,
            "p_ip_hash": client_hash, "p_created_epoch": epoch,
        })
        return {
            "visit_id": result["visit_id"],
            "visit_number": int(result["visit_number"]),
            "full_name": result["full_name"],
            "phone": result["phone"],
            "visit_date": result["visit_date"],
            "visit_time": result["visit_time"],
            "created_at": result["created_at"],
            "duplicate": bool(result.get("duplicate", False)),
        }

    import sqlite3
    from .visits import DUPLICATE_WINDOW_SECONDS

    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM visits WHERE request_token = ?", (request_token,)
        ).fetchone()
        if existing is not None:
            conn.commit()
            return _visit_dict(existing, True)

        last = conn.execute(
            "SELECT * FROM visits WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone,)
        ).fetchone()
        if last is not None and epoch - float(last["created_epoch"]) < DUPLICATE_WINDOW_SECONDS:
            conn.commit()
            return _visit_dict(last, True)

        customer = conn.execute("SELECT id FROM customers WHERE phone = ?", (phone,)).fetchone()
        if customer is None:
            cursor = conn.execute(
                "INSERT INTO customers(full_name, phone, created_at, updated_at) VALUES(?,?,?,?)",
                (full_name, phone, created_at, created_at),
            )
            customer_id = cursor.lastrowid
        else:
            customer_id = customer["id"]
            conn.execute(
                "UPDATE customers SET full_name = ?, updated_at = ? WHERE id = ?",
                (full_name, created_at, customer_id),
            )

        visit_number = conn.execute(
            "SELECT COALESCE(MAX(visit_number), 0) + 1 FROM visits WHERE customer_id = ?",
            (customer_id,),
        ).fetchone()[0]
        cursor = conn.execute(
            """INSERT INTO visits(customer_id, visit_number, full_name, phone, visit_date,
                                  visit_time, created_at, created_epoch, request_token, ip_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                customer_id, visit_number, full_name, phone,
                time.strftime("%Y-%m-%d", time.localtime(epoch)),
                time.strftime("%H:%M:%S", time.localtime(epoch)),
                created_at, epoch, request_token, client_hash,
            ),
        )
        conn.commit()
        return _visit_dict(
            conn.execute("SELECT * FROM visits WHERE id = ?", (cursor.lastrowid,)).fetchone()
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        row = conn.execute("SELECT * FROM visits WHERE request_token = ?", (request_token,)).fetchone()
        if row is not None:
            return _visit_dict(row, True)
        raise
    finally:
        conn.close()


def _visit_dict(row: Any, duplicate: bool = False) -> dict[str, Any]:
    return {
        "visit_id": row["id"],
        "visit_number": row["visit_number"],
        "full_name": row["full_name"],
        "phone": row["phone"],
        "visit_date": row["visit_date"],
        "visit_time": row["visit_time"],
        "created_at": row["created_at"],
        "duplicate": duplicate,
    }


def _visit_params(filters: dict[str, str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    q = filters.get("q", "").strip()
    if q:
        digits = re.sub(r"\D", "", q)
        clauses = [f"full_name.like.*{q}*", f"phone.like.*{q}*"]
        if digits:
            clauses.append(f"phone.like.*{digits}*")
        params["or"] = "(" + ",".join(clauses) + ")"
    if filters.get("desde"):
        params["visit_date"] = f"gte.{filters['desde']}"
    # El filtro "hasta" se aplica en Python (_matches_filters).
    return params


def _matches_filters(row: dict[str, Any], filters: dict[str, str]) -> bool:
    hasta = filters.get("hasta", "").strip()
    if hasta and str(row.get("visit_date", "")) > hasta:
        return False
    return True


# ------------------------------------------------------------- consultas ----

def count_customers() -> int:
    if supabase_enabled():
        return len(_rows("customers", {"select": "id"}))
    return int(_scalar("SELECT COUNT(*) FROM customers"))


def count_products_available() -> int:
    if supabase_enabled():
        return len(_rows("products", {"select": "id", "available": "eq.true"}))
    return int(_scalar("SELECT COUNT(*) FROM products WHERE available = 1"))


def count_demo_products() -> int:
    if supabase_enabled():
        return len(_rows("products", {"select": "id", "is_demo": "eq.true"}))
    return int(_scalar("SELECT COUNT(*) FROM products WHERE is_demo = 1"))


def count_visits(filters: dict[str, str] | None = None) -> int:
    filters = filters or {}
    if supabase_enabled():
        rows = _rows("visits", {"select": "id", **_visit_params(filters)})
        return sum(1 for r in rows if _matches_filters(r, filters))
    where, params = _local_visit_where(filters)
    return int(_scalar("SELECT COUNT(*) FROM visits " + where, params))


def list_visits(filters: dict[str, str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    if supabase_enabled():
        params: dict[str, Any] = {"select": "*", "order": "id.desc", **_visit_params(filters)}
        if limit:
            params["limit"] = limit
        return [r for r in _rows("visits", params) if _matches_filters(r, filters)]
    where, params = _local_visit_where(filters)
    sql = "SELECT * FROM visits " + where + " ORDER BY id DESC"
    if limit:
        sql += " LIMIT ?"
        params = params + (limit,)
    return _local_all(sql, params)


def _local_visit_where(filters: dict[str, str]) -> tuple[str, tuple[Any, ...]]:
    sql = "WHERE 1=1"
    params: list[Any] = []
    q = filters.get("q", "").strip()
    if q:
        like = _like(q)
        digits = re.sub(r"\D", "", q)
        if digits:
            sql += " AND (full_name LIKE ? OR phone LIKE ? OR phone LIKE ?)"
            params.extend([like, like, _like(digits)])
        else:
            sql += " AND (full_name LIKE ? OR phone LIKE ?)"
            params.extend([like, like])
    if filters.get("desde"):
        sql += " AND visit_date >= ?"
        params.append(filters["desde"])
    if filters.get("hasta"):
        sql += " AND visit_date <= ?"
        params.append(filters["hasta"])
    return sql, tuple(params)


def top_customers(limit: int = 6) -> list[dict[str, Any]]:
    if supabase_enabled():
        customers = _rows("customers", {"select": "id,full_name,phone"})
        visits = _rows("visits", {"select": "customer_id,visit_date"})
        totals: dict[str, list[Any]] = {}
        for v in visits:
            key = str(v["customer_id"])
            if key not in totals:
                totals[key] = [0, v["visit_date"], v["visit_date"]]
            totals[key][0] += 1
            if v["visit_date"] < totals[key][1]:
                totals[key][1] = v["visit_date"]
            if v["visit_date"] > totals[key][2]:
                totals[key][2] = v["visit_date"]
        result = []
        for c in customers:
            t = totals.get(str(c["id"]))
            if t:
                result.append({
                    "id": c["id"], "full_name": c["full_name"], "phone": c["phone"],
                    "total": t[0], "primera": t[1], "ultima": t[2],
                })
        result.sort(key=lambda r: (-r["total"], r["full_name"]))
        return result[:limit]
    return _local_all(
        """SELECT c.id, c.full_name, c.phone, COUNT(v.id) AS total,
                  MIN(v.visit_date) AS primera, MAX(v.visit_date) AS ultima
           FROM customers c JOIN visits v ON v.customer_id = c.id
           GROUP BY c.id ORDER BY total DESC, c.full_name LIMIT ?""",
        (limit,),
    )


def visit_daily_series(since: str) -> dict[str, int]:
    if supabase_enabled():
        rows = _rows("visits", {"select": "visit_date", "visit_date": f"gte.{since}"})
        series: dict[str, int] = {}
        for r in rows:
            key = str(r["visit_date"])
            series[key] = series.get(key, 0) + 1
        return series
    return {
        str(r["visit_date"]): r["n"]
        for r in _local_all(
            "SELECT visit_date, COUNT(*) AS n FROM visits WHERE visit_date >= ? GROUP BY visit_date",
            (since,),
        )
    }


def search_customers(q: str) -> list[dict[str, Any]]:
    if supabase_enabled():
        customers = _rows("customers", {"select": "id,full_name,phone", "order": "full_name.asc"})
        visits = _rows("visits", {"select": "customer_id,visit_date"})
        totals: dict[str, list[Any]] = {}
        for v in visits:
            key = str(v["customer_id"])
            if key not in totals:
                totals[key] = [0, v["visit_date"], v["visit_date"]]
            totals[key][0] += 1
            if v["visit_date"] < totals[key][1]:
                totals[key][1] = v["visit_date"]
            if v["visit_date"] > totals[key][2]:
                totals[key][2] = v["visit_date"]
        needle = q.strip().lower()
        result = []
        for c in customers:
            if needle and needle not in c["full_name"].lower() and needle not in c["phone"]:
                continue
            t = totals.get(str(c["id"]), [0, None, None])
            result.append({
                "id": c["id"], "full_name": c["full_name"], "phone": c["phone"],
                "total": t[0], "primera": t[1], "ultima": t[2],
            })
        result.sort(key=lambda r: (-r["total"], r["full_name"]))
        return result
    like = _like(q)
    return _local_all(
        """SELECT c.id, c.full_name, c.phone, COUNT(v.id) AS total,
                  MIN(v.visit_date) AS primera, MAX(v.visit_date) AS ultima
           FROM customers c LEFT JOIN visits v ON v.customer_id = c.id
           WHERE c.full_name LIKE ? OR c.phone LIKE ?
           GROUP BY c.id ORDER BY total DESC, c.full_name""",
        (like, like),
    )


def all_customers_with_totals() -> list[dict[str, Any]]:
    if supabase_enabled():
        return search_customers("")
    return _local_all(
        """SELECT c.id, c.full_name, c.phone, COUNT(v.id) AS total,
                  MIN(v.visit_date) AS primera, MAX(v.visit_date) AS ultima
           FROM customers c LEFT JOIN visits v ON v.customer_id = c.id
           GROUP BY c.id ORDER BY c.full_name"""
    )


def get_customer(customer_id: int) -> dict[str, Any] | None:
    if supabase_enabled():
        return _row("customers", {"id": f"eq.{customer_id}"})
    return _local_one("SELECT * FROM customers WHERE id = ?", (customer_id,))


def customer_visits(customer_id: int) -> list[dict[str, Any]]:
    if supabase_enabled():
        return _rows("visits", {
            "customer_id": f"eq.{customer_id}", "order": "visit_number.desc",
        })
    return _local_all(
        "SELECT * FROM visits WHERE customer_id = ? ORDER BY visit_number DESC", (customer_id,)
    )


def delete_visits(filters: dict[str, str] | None = None) -> int:
    """Elimina todas las visitas o solo las que coinciden con el filtro."""
    filters = filters or {}
    if supabase_enabled():
        ids: list[str] = []
        offset = 0
        while True:
            page = _rows("visits", {
                "select": "id,visit_date", "order": "id.asc",
                "limit": 1000, "offset": offset, **_visit_params(filters),
            })
            ids.extend(str(r["id"]) for r in page if _matches_filters(r, filters))
            if len(page) < 1000:
                break
            offset += 1000
        for visit_id in ids:
            _delete("visits", visit_id)
        return len(ids)
    where, params = _local_visit_where(filters)
    conn = db.connect()
    try:
        cursor = conn.execute("DELETE FROM visits " + where, params)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def delete_customer_visits(customer_id: int) -> int:
    """Borra todo el historial de visitas de un cliente, sin borrar el cliente."""
    if supabase_enabled():
        ids = [str(r["id"]) for r in _rows(
            "visits", {"select": "id", "customer_id": f"eq.{customer_id}"}
        )]
        for visit_id in ids:
            _delete("visits", visit_id)
        return len(ids)
    conn = db.connect()
    try:
        cursor = conn.execute("DELETE FROM visits WHERE customer_id = ?", (customer_id,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def delete_visit(visit_id: int) -> int:
    """Elimina una visita concreta."""
    if supabase_enabled():
        if _row("visits", {"id": f"eq.{visit_id}"}) is None:
            return 0
        _delete("visits", visit_id)
        return 1
    conn = db.connect()
    try:
        cursor = conn.execute("DELETE FROM visits WHERE id = ?", (visit_id,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def delete_customer(customer_id: int) -> int | None:
    """Borra un cliente y todo su historial. Devuelve las visitas borradas o None si no existe."""
    if count_customer_orders(customer_id):
        raise ValueError('No se puede eliminar: el cliente tiene pedidos registrados.')
    if supabase_enabled():
        if _row("customers", {"id": f"eq.{customer_id}"}) is None:
            return None
        visitas = _rows("visits", {"select": "id", "customer_id": f"eq.{customer_id}"})
        for visita in visitas:
            _delete("visits", visita["id"])
        _delete("customers", customer_id)
        return len(visitas)

    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone() is None:
            conn.rollback()
            return None
        total = conn.execute(
            "SELECT COUNT(*) FROM visits WHERE customer_id = ?", (customer_id,)
        ).fetchone()[0]
        conn.execute("DELETE FROM visits WHERE customer_id = ?", (customer_id,))
        conn.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        conn.commit()
        return int(total)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- pedidos ---

ESTADOS_PEDIDO = ('pendiente', 'aceptada', 'preparando', 'lista', 'entregada', 'rechazada', 'cancelada')
METODOS_PAGO = ('efectivo', 'transferencia')
ESTADOS_PAGO = ('pendiente', 'comprobante_recibido', 'en_verificacion',
                'aprobado', 'rechazado', 'reembolsado')
ESTADOS_SIN_VENTA = ('rechazada', 'cancelada')
ESTADOS_CON_COMPROBANTE = ('pendiente', 'comprobante_recibido', 'en_verificacion', 'rechazado')
CAMPOS_CUENTA = ('bank_name', 'holder_name', 'account_number', 'account_type',
                 'id_number', 'instructions', 'active', 'sort_order')


def _money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _importe(value: Any) -> float:
    return float(_money(value))


def customer_for_visit(visit_id: int) -> int | None:
    """Resuelve el cliente desde la visita firmada; nunca se acepta desde el navegador."""
    if supabase_enabled():
        row = _row("visits", {"select": "customer_id", "id": f"eq.{visit_id}"})
        return int(row["customer_id"]) if row else None
    value = _scalar("SELECT customer_id FROM visits WHERE id = ?", (visit_id,))
    return int(value) if value else None


def create_order(visit_id: int, items: list[dict[str, Any]], idempotency_key: str,
                 delivery_method: str = 'retiro', address: dict[str, Any] | None = None,
                 notes: str = '', payment_method: str = 'efectivo',
                 bank_account_id: int | None = None) -> dict[str, Any]:
    """Crea pedido + artículos + pago de forma atómica con precios del servidor."""
    if not idempotency_key or len(idempotency_key) > 120:
        raise ValueError('Clave de pedido no válida.')
    if delivery_method not in ('delivery', 'retiro'):
        raise ValueError('Modalidad de entrega no válida.')
    if payment_method not in METODOS_PAGO:
        raise ValueError('Método de pago no válido.')
    if bank_account_id is not None:
        try:
            bank_account_id = int(bank_account_id)
        except (TypeError, ValueError):
            raise ValueError('Cuenta bancaria no válida.')
    if payment_method == 'transferencia':
        if get_active_payment_account(bank_account_id) is None:
            raise ValueError('Selecciona una cuenta bancaria disponible.')
    else:
        bank_account_id = None
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise ValueError('El pedido debe tener entre 1 y 50 artículos.')

    limpios: list[dict[str, Any]] = []
    vistos: set[int] = set()
    for raw in items:
        try:
            product_id = int(raw.get('product_id'))
            quantity = int(raw.get('quantity'))
        except (TypeError, ValueError):
            raise ValueError('Artículo del pedido no válido.')
        if product_id <= 0 or not 1 <= quantity <= 99:
            raise ValueError('Cantidad no válida.')
        if product_id in vistos:
            raise ValueError('Hay un producto repetido en el pedido.')
        vistos.add(product_id)
        nota = str(raw.get('notes') or '').strip()[:200]
        limpios.append({'product_id': product_id, 'quantity': quantity, 'notes': nota})

    cliente_id = customer_for_visit(visit_id)
    if cliente_id is None:
        raise ValueError('Visita no válida. Debes registrarte de nuevo.')
    cliente = get_customer(cliente_id)
    if cliente is None:
        raise ValueError('Cliente no encontrado.')
    nota_general = str(notes or '').strip()[:500]

    if delivery_method == 'delivery':
        if not isinstance(address, dict) or not str(address.get('address_line1') or '').strip():
            raise ValueError('La dirección de entrega es obligatoria.')
        direccion = {
            'address_line1': str(address.get('address_line1')).strip()[:200],
            'address_line2': str(address.get('address_line2') or '').strip()[:200],
            'city': str(address.get('city') or '').strip()[:80],
            'province': str(address.get('province') or '').strip()[:80],
            'notes': str(address.get('notes') or '').strip()[:200],
        }
    else:
        direccion = None

    if supabase_enabled():
        return _rpc('crear_pedido', {
            'p_visit_id': visit_id,
            'p_items': limpios,
            'p_idempotencia_key': idempotency_key,
            'p_delivery_method': delivery_method,
            'p_address': direccion,
            'p_notes': nota_general,
            'p_payment_method': payment_method,
            'p_bank_account_id': bank_account_id,
        })

    import sqlite3
    conn = db.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        anterior = conn.execute(
            'SELECT id, order_number, total FROM orders WHERE idempotency_key = ?', (idempotency_key,)
        ).fetchone()
        if anterior is not None:
            conn.commit()
            return {
                'order_id': anterior['id'], 'order_number': anterior['order_number'],
                'total': _importe(anterior['total']), 'duplicate': True,
            }

        ahora = now_local().isoformat(timespec='seconds')
        direccion_id = None
        if direccion:
            cursor = conn.execute(
                """INSERT INTO delivery_addresses(customer_id, address_line1, address_line2,
                                                   city, province, notes, created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (cliente_id, direccion['address_line1'], direccion['address_line2'],
                 direccion['city'], direccion['province'], direccion['notes'], ahora),
            )
            direccion_id = cursor.lastrowid

        cursor = conn.execute(
            """INSERT INTO orders(customer_id, visit_id, delivery_address_id, order_number,
                                  status, delivery_method, customer_name_snapshot,
                                  customer_phone_snapshot, notes, subtotal, discount,
                                  delivery_fee, total, idempotency_key, bank_account_id,
                                  created_at, updated_at)
               VALUES(?,?,?,?,'pendiente',?,?,?,?,0,0,0,0,?,?,?,?)""",
            (cliente_id, visit_id, direccion_id, 'TEMP-' + idempotency_key,
             delivery_method, cliente['full_name'], cliente['phone'], nota_general,
             idempotency_key, bank_account_id, ahora, ahora),
        )
        orden_id = cursor.lastrowid
        subtotal = Decimal('0.00')
        for indice, item in enumerate(limpios):
            prod = conn.execute(
                """SELECT p.id, p.name, p.price, p.available, c.active
                   FROM products p JOIN categories c ON c.id = p.category_id
                   WHERE p.id = ?""", (item['product_id'],)
            ).fetchone()
            if prod is None or prod['available'] != 1 or prod['active'] != 1 or _money(prod['price']) <= 0:
                raise ValueError('Producto no disponible o precio por definir.')
            precio = _money(prod['price'])
            linea = (precio * item['quantity']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            subtotal += linea
            conn.execute(
                """INSERT INTO order_items(order_id, product_id, product_name_snapshot,
                                            unit_price, quantity, notes, line_total, sort_order)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (orden_id, prod['id'], prod['name'], float(precio), item['quantity'],
                 item['notes'], float(linea), indice),
            )

        numero = 'PV-' + now_local().strftime('%Y%m%d') + '-' + str(orden_id).zfill(6)
        conn.execute(
            'UPDATE orders SET order_number=?, subtotal=?, total=?, updated_at=? WHERE id=?',
            (numero, float(subtotal), float(subtotal), ahora, orden_id),
        )
        conn.execute(
            """INSERT INTO payments(order_id, method, status, amount, created_at)
               VALUES(?, ?, 'pendiente', ?, ?)""",
            (orden_id, payment_method, float(subtotal), ahora),
        )
        conn.commit()
        return {
            'order_id': orden_id, 'order_number': numero,
            'total': _importe(subtotal), 'duplicate': False,
        }
    except sqlite3.Error as exc:
        conn.rollback()
        raise RuntimeError('No se pudo guardar el pedido. Inténtalo de nuevo.') from exc
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _order_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': int(row['id']),
        'numero': row.get('order_number', ''),
        'cliente_id': int(row['customer_id']),
        'cliente': row.get('customer_name_snapshot', ''),
        'telefono': row.get('customer_phone_snapshot', ''),
        'estado': row['status'],
        'entrega': row.get('delivery_method', 'retiro'),
        'notas': row.get('notes', ''),
        'subtotal': _importe(row.get('subtotal')),
        'descuento': _importe(row.get('discount')),
        'envio': _importe(row.get('delivery_fee')),
        'total': _importe(row.get('total')),
        'creado': row.get('created_at', ''),
        'actualizado': row.get('updated_at', ''),
        'banco_id': int(row['bank_account_id']) if row.get('bank_account_id') else None,
    }


def _order_rows(filters: dict[str, str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    filters = filters or {}
    q = str(filters.get('q') or '').strip().lower()
    estado = str(filters.get('estado') or '').strip()
    pago = str(filters.get('pago') or '').strip()
    desde = str(filters.get('desde') or '').strip()
    hasta = str(filters.get('hasta') or '').strip()
    limite = min(max(int(limit), 1), 1000)

    pagos = _payment_rows()
    if supabase_enabled():
        params: dict[str, Any] = {'select': '*', 'order': 'created_at.desc', 'limit': limite}
        if estado:
            params['status'] = f'eq.{estado}'
        rows = [_order_dict(r) for r in _rows('orders', params)]
    else:
        sql = 'SELECT * FROM orders WHERE 1=1'
        params_sql: list[Any] = []
        if estado:
            sql += ' AND status = ?'
            params_sql.append(estado)
        sql += ' ORDER BY created_at DESC LIMIT ?'
        params_sql.append(limite)
        rows = [_order_dict(dict(r)) for r in db.all_rows(sql, tuple(params_sql))]

    proofs = list_payment_proofs()
    bancos = {c['id']: c['banco'] for c in list_payment_accounts()}
    for row in rows:
        pago_row = pagos.get(row['id'])
        row['pago_estado'] = pago_row['status'] if pago_row else 'pendiente'
        row['metodo_pago'] = pago_row['method'] if pago_row else ''
        row['pago_nota'] = (pago_row or {}).get('review_note', '') or ''
        row['banco'] = bancos.get(row.get('banco_id'), '')
        row['comprobante_enviado'] = proofs.get(row['id'], {}).get('enviado', '')

    return [r for r in rows
            if (not pago or r['pago_estado'] == pago)
            and (not q or q in r['numero'].lower() or q in r['cliente'].lower()
                 or q in r['telefono'].lower())
            and (not desde or str(r['creado'])[:10] >= desde)
            and (not hasta or str(r['creado'])[:10] <= hasta)]


def list_orders(filters: dict[str, str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return _order_rows(filters, limit)


def count_orders(filters: dict[str, str] | None = None) -> int:
    return len(_order_rows(filters, 1000))


def get_order(order_id: int) -> dict[str, Any] | None:
    if supabase_enabled():
        row = _row('orders', {'id': f'eq.{order_id}'})
        if row is None:
            return None
        items = _rows('order_items', {
            'order_id': f'eq.{order_id}', 'order': 'sort_order.asc,id.asc', 'limit': 1000,
        })
        pago = _row('payments', {'order_id': f'eq.{order_id}', 'limit': 1})
        direccion = None
        if row.get('delivery_address_id'):
            direccion = _row('delivery_addresses', {'id': f'eq.{row["delivery_address_id"]}'})
    else:
        raw = db.one('SELECT * FROM orders WHERE id = ?', (order_id,))
        if raw is None:
            return None
        row = dict(raw)
        items = [dict(r) for r in db.all_rows(
            'SELECT * FROM order_items WHERE order_id = ? ORDER BY sort_order, id', (order_id,)
        )]
        pago_raw = db.one('SELECT * FROM payments WHERE order_id = ? ORDER BY id LIMIT 1', (order_id,))
        pago = dict(pago_raw) if pago_raw else None
        direccion = None
        if row.get('delivery_address_id'):
            dir_raw = db.one('SELECT * FROM delivery_addresses WHERE id = ?', (row['delivery_address_id'],))
            direccion = dict(dir_raw) if dir_raw else None

    data = _order_dict(row)
    data['items'] = [{
        'id': int(i['id']), 'producto_id': int(i['product_id']),
        'nombre': i['product_name_snapshot'], 'precio': _importe(i['unit_price']),
        'cantidad': int(i['quantity']), 'notas': i.get('notes', ''),
        'subtotal': _importe(i['line_total']),
    } for i in items]
    data['pago'] = None if pago is None else {
        'metodo': pago['method'], 'estado': pago['status'],
        'monto': _importe(pago['amount']), 'referencia': pago.get('provider_payment_id', ''),
        'pagado_en': pago.get('paid_at'),
        'nota': pago.get('review_note', '') or '',
        'revisado_en': pago.get('reviewed_at'),
        'confirmado_por': pago.get('confirmed_by'),
    }
    proof = get_payment_proof(order_id)
    data['comprobante'] = None if proof is None else {
        'nombre': proof['nombre'], 'enviado': proof['enviado'], 'tamano': proof['tamano'],
    }
    data['comprobante_enviado'] = proof['enviado'] if proof else ''
    cuenta = get_active_payment_account(row.get('bank_account_id'))
    if cuenta is None and supabase_enabled():
        cuenta = next((c for c in list_payment_accounts()
                       if c['id'] == (row.get('bank_account_id') or -1)), None)
    data['banco'] = cuenta['banco'] if cuenta else ''
    data['direccion'] = None if direccion is None else {
        'linea1': direccion.get('address_line1', ''),
        'linea2': direccion.get('address_line2', ''),
        'ciudad': direccion.get('city', ''),
        'provincia': direccion.get('province', ''),
        'notas': direccion.get('notes', ''),
    }
    return data


def delete_order(order_id: int) -> dict[str, Any] | None:
    """Borra un pedido y, en cascada, sus artículos y pago. Devuelve None si no existe."""
    if supabase_enabled():
        row = _row('orders', {
            'id': f'eq.{order_id}', 'select': 'id,order_number,delivery_address_id',
        })
        if row is None:
            return None
        numero = row.get('order_number', '')
        direccion_id = row.get('delivery_address_id')
        _delete('orders', order_id)
        if direccion_id and not _rows('orders', {
            'select': 'id', 'delivery_address_id': f'eq.{direccion_id}', 'limit': 1,
        }):
            _delete('delivery_addresses', direccion_id)
        return {'eliminado': 1, 'numero': numero}

    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT order_number, delivery_address_id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        numero = row['order_number']
        direccion_id = row['delivery_address_id']
        conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        if direccion_id and conn.execute(
            "SELECT 1 FROM orders WHERE delivery_address_id = ? LIMIT 1", (direccion_id,)
        ).fetchone() is None:
            conn.execute("DELETE FROM delivery_addresses WHERE id = ?", (direccion_id,))
        conn.commit()
        return {'eliminado': 1, 'numero': numero}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def customer_orders(customer_id: int) -> list[dict[str, Any]]:
    if supabase_enabled():
        rows = _rows('orders', {
            'customer_id': f'eq.{customer_id}', 'order': 'created_at.desc', 'limit': 1000,
        })
        pedidos = [_order_dict(r) for r in rows]
    else:
        pedidos = [_order_dict(dict(r)) for r in db.all_rows(
            'SELECT * FROM orders WHERE customer_id = ? ORDER BY created_at DESC', (customer_id,)
        )]
    pagos = _payment_rows()
    bancos = {c['id']: c['banco'] for c in list_payment_accounts()}
    for pedido in pedidos:
        pago = pagos.get(pedido['id'], {})
        pedido['pago_estado'] = pago.get('status', 'pendiente')
        pedido['metodo_pago'] = pago.get('method', '')
        pedido['pago_nota'] = pago.get('review_note', '') or ''
        pedido['banco'] = bancos.get(pedido.get('banco_id'), '')
    return pedidos


def count_customer_orders(customer_id: int) -> int:
    if supabase_enabled():
        return len(_rows('orders', {'select': 'id', 'customer_id': f'eq.{customer_id}', 'limit': 1000}))
    return int(_scalar('SELECT COUNT(*) FROM orders WHERE customer_id = ?', (customer_id,)))


def count_product_orders(product_id: int) -> int:
    if supabase_enabled():
        return len(_rows('order_items', {'select': 'id', 'product_id': f'eq.{product_id}', 'limit': 1000}))
    return int(_scalar('SELECT COUNT(*) FROM order_items WHERE product_id = ?', (product_id,)))


# ------------------------------------------------------- cuentas bancarias ---

def _cuenta_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': int(row['id']),
        'banco': row.get('bank_name', ''),
        'titular': row.get('holder_name', '') or '',
        'numero': row.get('account_number', '') or '',
        'tipo': row.get('account_type', '') or '',
        'identificacion': row.get('id_number', '') or '',
        'instrucciones': row.get('instructions', '') or '',
        'activa': int(row.get('active', 0)),
        'orden': int(row.get('sort_order', 0) or 0),
    }


def _limpiar_cuenta(payload: dict[str, Any]) -> dict[str, Any]:
    banco = str(payload.get('banco') or payload.get('bank_name') or '').strip()[:80]
    if not banco:
        raise ValueError('Escribe el nombre del banco.')
    return {
        'bank_name': banco,
        'holder_name': str(payload.get('titular') or payload.get('holder_name') or '').strip()[:120],
        'account_number': str(payload.get('numero') or payload.get('account_number') or '').strip()[:40],
        'account_type': str(payload.get('tipo') or payload.get('account_type') or '').strip()[:40],
        'id_number': str(payload.get('identificacion') or payload.get('id_number') or '').strip()[:40],
        'instructions': str(payload.get('instrucciones') or payload.get('instructions') or '').strip()[:500],
        'active': 1 if payload.get('activa', payload.get('active', False)) else 0,
        'sort_order': max(-99, min(99, int(payload.get('orden', payload.get('sort_order', 0)) or 0))),
    }


def list_payment_accounts() -> list[dict[str, Any]]:
    if supabase_enabled():
        rows = _rows('payment_accounts', {'order': 'sort_order.asc,id.asc', 'limit': 200})
    else:
        rows = _local_all('SELECT * FROM payment_accounts ORDER BY sort_order, id')
    return [_cuenta_dict(r) for r in rows]


def public_payment_accounts() -> list[dict[str, Any]]:
    """Solo cuentas activas y completas: el cliente nunca ve borradores."""
    return [c for c in list_payment_accounts()
            if c['activa'] and c['titular'] and c['numero']]


def get_active_payment_account(account_id: Any) -> dict[str, Any] | None:
    if account_id is None:
        return None
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return None
    for cuenta in public_payment_accounts():
        if cuenta['id'] == account_id:
            return cuenta
    return None


def create_payment_account(payload: dict[str, Any]) -> dict[str, Any]:
    datos = _limpiar_cuenta(payload)
    ahora = now_local().isoformat(timespec='seconds')
    datos['created_at'] = ahora
    datos['updated_at'] = ahora
    if supabase_enabled():
        return _cuenta_dict(_insert('payment_accounts', datos))
    conn = db.connect()
    try:
        cols = ','.join(datos)
        marks = ','.join('?' for _ in datos)
        cursor = conn.execute(f'INSERT INTO payment_accounts({cols}) VALUES({marks})', tuple(datos.values()))
        return _cuenta_dict(dict(db.one('SELECT * FROM payment_accounts WHERE id=?', (cursor.lastrowid,))))
    finally:
        conn.close()


def update_payment_account(account_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    datos = _limpiar_cuenta(payload)
    datos['updated_at'] = now_local().isoformat(timespec='seconds')
    if supabase_enabled():
        if _row('payment_accounts', {'id': f'eq.{account_id}', 'select': 'id'}) is None:
            return None
        _update('payment_accounts', account_id, datos)
    else:
        actual = db.one('SELECT id FROM payment_accounts WHERE id=?', (account_id,))
        if actual is None:
            return None
        asignaciones = ','.join(f'{k}=?' for k in datos)
        db.execute(f'UPDATE payment_accounts SET {asignaciones} WHERE id=?', (*datos.values(), account_id))
    fila = _row('payment_accounts', {'id': f'eq.{account_id}'}) if supabase_enabled() else db.one(
        'SELECT * FROM payment_accounts WHERE id=?', (account_id,))
    return _cuenta_dict(dict(fila)) if fila else None


def delete_payment_account(account_id: int) -> bool:
    if supabase_enabled():
        if _row('payment_accounts', {'id': f'eq.{account_id}', 'select': 'id'}) is None:
            return False
        _delete('payment_accounts', account_id)
        return True
    borrados = db.execute('DELETE FROM payment_accounts WHERE id=?', (account_id,))
    return bool(borrados)


# ------------------------------------------------------------ comprobantes ---

def _proof_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': int(row['id']), 'pedido_id': int(row['order_id']),
        'cliente_id': int(row['customer_id']) if row.get('customer_id') else None,
        'cuenta_id': int(row['bank_account_id']) if row.get('bank_account_id') else None,
        'nombre': row.get('file_name', ''), 'ruta': row.get('file_path', ''),
        'tipo': row.get('content_type', ''), 'tamano': int(row.get('size_bytes', 0) or 0),
        'enviado': row.get('uploaded_at', ''),
    }


def get_payment_proof(order_id: int) -> dict[str, Any] | None:
    if supabase_enabled():
        row = _row('payment_proofs', {'order_id': f'eq.{order_id}', 'limit': 1})
    else:
        row = db.one('SELECT * FROM payment_proofs WHERE order_id=?', (order_id,))
    return _proof_dict(dict(row)) if row else None


def list_payment_proofs() -> dict[int, dict[str, Any]]:
    if supabase_enabled():
        rows = _rows('payment_proofs', {'limit': 1000})
    else:
        rows = _local_all('SELECT * FROM payment_proofs')
    return {int(r['order_id']): _proof_dict(r) for r in rows}


def save_payment_proof(order_id: int, customer_id: int, bank_account_id: int | None,
                       nombre: str, ruta: str, content_type: str, size: int) -> dict[str, Any]:
    """Un comprobante por pedido; reemplazar el anterior no cambia el estado del pago."""
    ahora = now_local().isoformat(timespec='seconds')
    datos = {
        'order_id': order_id, 'customer_id': customer_id, 'bank_account_id': bank_account_id,
        'file_name': nombre[:120], 'file_path': ruta, 'content_type': content_type,
        'size_bytes': int(size), 'uploaded_at': ahora,
    }
    anterior = get_payment_proof(order_id)
    if supabase_enabled():
        if anterior:
            _update('payment_proofs', anterior['id'], datos)
            return get_payment_proof(order_id)
        return _proof_dict(_insert('payment_proofs', datos))
    conn = db.connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        if anterior:
            columnas = ','.join(f'{k}=?' for k in datos)
            conn.execute(f'UPDATE payment_proofs SET {columnas} WHERE id=?', (*datos.values(), anterior['id']))
        else:
            columnas = ','.join(datos)
            marks = ','.join('?' for _ in datos)
            conn.execute(f'INSERT INTO payment_proofs({columnas}) VALUES({marks})', tuple(datos.values()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_payment_proof(order_id)


def delete_payment_proof(proof_id: int) -> None:
    if supabase_enabled():
        _delete('payment_proofs', proof_id)
    else:
        db.execute('DELETE FROM payment_proofs WHERE id=?', (proof_id,))


# ----------------------------------------------------------- notificaciones --

def listar_suscripciones_push() -> list[dict[str, Any]]:
    if supabase_enabled():
        return _rows('admin_push_subscriptions', {'limit': 500})
    return _local_all('SELECT * FROM admin_push_subscriptions')


def add_push_subscription(admin_id: int, endpoint: str, p256dh: str, auth: str) -> dict[str, Any]:
    datos = {'admin_id': admin_id, 'endpoint': endpoint, 'p256dh': p256dh, 'auth': auth,
             'created_at': now_local().isoformat(timespec='seconds')}
    if supabase_enabled():
        anterior = _row('admin_push_subscriptions', {'endpoint': f'eq.{endpoint}'})
        if anterior:
            _update('admin_push_subscriptions', anterior['id'],
                    {'admin_id': admin_id, 'p256dh': p256dh, 'auth': auth})
            return dict(anterior)
        return _insert('admin_push_subscriptions', datos)
    conn = db.connect()
    try:
        anterior = db.one('SELECT id FROM admin_push_subscriptions WHERE endpoint=?', (endpoint,))
        if anterior:
            db.execute('UPDATE admin_push_subscriptions SET admin_id=?, p256dh=?, auth=? WHERE id=?',
                       (admin_id, p256dh, auth, anterior['id']))
        else:
            db.execute('INSERT INTO admin_push_subscriptions(admin_id, endpoint, p256dh, auth, created_at) '
                       'VALUES(?,?,?,?,?)', (admin_id, endpoint, p256dh, auth, datos['created_at']))
    finally:
        conn.close()
    return datos


def remove_push_subscription(endpoint: str) -> int:
    if supabase_enabled():
        anterior = _row('admin_push_subscriptions', {'endpoint': f'eq.{endpoint}', 'select': 'id'})
        if anterior is None:
            return 0
        _delete('admin_push_subscriptions', anterior['id'])
        return 1
    return db.execute('DELETE FROM admin_push_subscriptions WHERE endpoint=?', (endpoint,))


def marcar_suscripcion_push(subscription_id: int) -> None:
    ahora = now_local().isoformat(timespec='seconds')
    if supabase_enabled():
        _update('admin_push_subscriptions', subscription_id, {'last_used_at': ahora})
    else:
        db.execute('UPDATE admin_push_subscriptions SET last_used_at=? WHERE id=?',
                   (ahora, subscription_id))


def borrar_suscripcion_push(subscription_id: int) -> None:
    if supabase_enabled():
        _delete('admin_push_subscriptions', subscription_id)
    else:
        db.execute('DELETE FROM admin_push_subscriptions WHERE id=?', (subscription_id,))


def _payment_rows() -> dict[int, dict[str, Any]]:
    if supabase_enabled():
        rows = _rows('payments', {'select': '*', 'limit': 1000})
    else:
        rows = [dict(r) for r in db.all_rows('SELECT * FROM payments', ())]
    return {int(r['order_id']): r for r in rows}


def _es_venta(order: dict[str, Any], pagos: dict[int, dict[str, Any]]) -> bool:
    pago = pagos.get(order['id'])
    return (pago is not None and pago['status'] == 'aprobado'
            and order['estado'] not in ESTADOS_SIN_VENTA)


def paid_orders() -> list[dict[str, Any]]:
    """Pedidos contabilizados como venta: pagados y no rechazados/cancelados."""
    orders = list_orders(limit=1000)
    pagos = _payment_rows()
    return [o for o in orders if _es_venta(o, pagos)]


def sales_summary() -> dict[str, Any]:
    """Ventas definitivas = pedidos marcados como pagados por el administrador."""
    orders = list_orders(limit=1000)
    pagos = _payment_rows()
    hoy = now_local()
    semana = (hoy - timedelta(days=6)).strftime('%Y-%m-%d')
    mes = hoy.strftime('%Y-%m')
    diario = hoy.strftime('%Y-%m-%d')

    vendidas = [o for o in orders if _es_venta(o, pagos)]
    def total_en(prefijo: str) -> float:
        return _importe(sum((o['total'] for o in vendidas if str(o['creado']).startswith(prefijo)), 0.0))

    ventas_dia = total_en(diario)
    ventas_semana = _importe(sum((o['total'] for o in vendidas if str(o['creado'])[:10] >= semana), 0.0))
    ventas_mes = total_en(mes)
    total_ventas = _importe(sum((o['total'] for o in vendidas), 0.0))

    pendientes = [o for o in orders if o['estado'] == 'pendiente'
                  and (pagos.get(o['id']) or {}).get('status') == 'pendiente']
    pagadas = vendidas
    entregadas = [o for o in vendidas if o['estado'] == 'entregada']
    serie: dict[str, float] = {}
    for dia in range(6, -1, -1):
        clave = (hoy - timedelta(days=dia)).strftime('%Y-%m-%d')
        serie[clave] = _importe(sum((o['total'] for o in vendidas if str(o['creado'])[:10] == clave), 0.0))

    # Los artículos se leen una sola vez y se filtran por pedidos ya contabilizados.
    if supabase_enabled():
        items = _rows('order_items', {'select': '*', 'limit': 1000})
        productos = _rows('products', {'select': 'id,category_id,name', 'limit': 1000})
        categorias = _rows('categories', {'select': 'id,name', 'limit': 1000})
    else:
        items = [dict(r) for r in db.all_rows('SELECT * FROM order_items', ())]
        productos = [dict(r) for r in db.all_rows('SELECT id,category_id,name FROM products', ())]
        categorias = [dict(r) for r in db.all_rows('SELECT id,name FROM categories', ())]
    cat_nombres = {int(c['id']): c['name'] for c in categorias}
    prod_cat = {int(p['id']): int(p['category_id']) for p in productos}
    prod_nombres = {int(p['id']): p['name'] for p in productos}
    ids_venta = {o['id'] for o in vendidas}
    top: dict[int, dict[str, Any]] = {}
    categorias_ingreso: dict[str, float] = {}
    for item in items:
        if int(item['order_id']) not in ids_venta:
            continue
        pid = int(item['product_id'])
        nombre = item.get('product_name_snapshot') or prod_nombres.get(pid, 'Producto')
        cant = int(item['quantity'])
        subtotal_item = _importe(item['line_total'])
        if pid not in top:
            top[pid] = {'producto_id': pid, 'nombre': nombre, 'cantidad': 0, 'total': 0.0}
        top[pid]['cantidad'] += cant
        top[pid]['total'] = _importe(top[pid]['total'] + subtotal_item)
        cat = cat_nombres.get(prod_cat.get(pid, -1), 'Sin categoría')
        categorias_ingreso[cat] = _importe(categorias_ingreso.get(cat, 0.0) + subtotal_item)

    return {
        'ventas_hoy': ventas_dia,
        'ventas_semana': ventas_semana,
        'ventas_mes': ventas_mes,
        'total_ventas': total_ventas,
        'total_pedidos': len(orders),
        'promedio_compra': _importe(Decimal(str(total_ventas)) / len(vendidas)) if vendidas else 0.0,
        'productos_top': sorted(top.values(), key=lambda x: (-x['cantidad'], x['nombre']))[:6],
        'ingresos_categoria': sorted(
            [{'categoria': k, 'total': v} for k, v in categorias_ingreso.items()],
            key=lambda x: -x['total'],
        ),
        'historial': [{
            'id': o['id'], 'numero': o['numero'], 'cliente': o['cliente'], 'fecha': o['creado'],
            'total': o['total'], 'estado': o['estado'],
        } for o in vendidas[:20]],
        'pendientes_pago': len(pendientes),
        'monto_pendiente': _importe(sum((o['total'] for o in pendientes), 0.0)),
        'pagadas': len(pagadas),
        'monto_pagado': total_ventas,
        'entregadas': len(entregadas),
        'monto_entregado': _importe(sum((o['total'] for o in entregadas), 0.0)),
        'serie_ventas': [{'fecha': k, 'total': v} for k, v in serie.items()],
    }


def customer_profile(customer_id: int) -> dict[str, Any] | None:
    cliente = get_customer(customer_id)
    if cliente is None:
        return None
    visitas = customer_visits(customer_id)
    pedidos = customer_orders(customer_id)
    pagos = _payment_rows()
    vendidas = [o for o in pedidos if _es_venta(o, pagos)]
    total = _importe(sum((o['total'] for o in vendidas), 0.0))
    ids_venta = {o['id'] for o in vendidas}

    if supabase_enabled():
        items = _rows('order_items', {'select': '*', 'limit': 1000})
    else:
        items = [dict(r) for r in db.all_rows('SELECT * FROM order_items', ())]
    favoritos: dict[int, dict[str, Any]] = {}
    for item in items:
        if int(item['order_id']) not in ids_venta:
            continue
        pid = int(item['product_id'])
        if pid not in favoritos:
            favoritos[pid] = {
                'producto_id': pid,
                'nombre': item.get('product_name_snapshot', 'Producto'),
                'cantidad': 0,
            }
        favoritos[pid]['cantidad'] += int(item['quantity'])

    return {
        'cliente': {
            'id': customer_id, 'nombre': cliente['full_name'], 'telefono': cliente['phone'],
        },
        'total_visitas': len(visitas),
        'ultima_visita': visitas[0]['visit_date'] if visitas else '—',
        'total_pedidos': len(pedidos),
        'total_gastado': total,
        'promedio_compra': _importe(Decimal(str(total)) / len(vendidas)) if vendidas else 0.0,
        'ultima_compra': vendidas[0]['creado'] if vendidas else '—',
        'pedidos': pedidos,
        'favoritos': sorted(favoritos.values(), key=lambda x: -x['cantidad'])[:6],
    }


def frequent_customers(limit: int = 6) -> list[dict[str, Any]]:
    orders = list_orders(limit=1000)
    pagos = _payment_rows()
    resumen: dict[int, dict[str, Any]] = {}
    for order in orders:
        if not _es_venta(order, pagos):
            continue
        cid = order['cliente_id']
        if cid not in resumen:
            resumen[cid] = {
                'cliente_id': cid, 'nombre': order['cliente'], 'telefono': order['telefono'],
                'pedidos': 0, 'total': 0.0, 'ultima': order['creado'],
            }
        data = resumen[cid]
        data['pedidos'] += 1
        data['total'] = _importe(data['total'] + order['total'])
        data['promedio'] = _importe(Decimal(str(data['total'])) / data['pedidos'])
    return sorted(resumen.values(), key=lambda x: (-x['total'], -x['pedidos']))[:limit]


def _set_payment(order_id: int, status: str, referencia: str = '',
                 admin_id: int | None = None, nota: str = '') -> None:
    """Cambia el estado del pago y deja auditoría de quién lo revisó/confirmó."""
    ahora = now_local().isoformat(timespec='seconds')
    cambios: dict[str, Any] = {'status': status}
    if status == 'aprobado':
        cambios['paid_at'] = ahora
        if referencia:
            cambios['provider'] = 'administrador'
            cambios['provider_payment_id'] = referencia[:120]
        if admin_id:
            cambios['confirmed_by'] = int(admin_id)
            cambios['confirmed_at'] = ahora
    elif status == 'pendiente':
        cambios['paid_at'] = None
    if status in ('comprobante_recibido', 'en_verificacion', 'rechazado'):
        if admin_id:
            cambios['reviewed_by'] = int(admin_id)
        cambios['reviewed_at'] = ahora
        cambios['review_note'] = str(nota or '').strip()[:500]
    if supabase_enabled():
        pago = _row('payments', {'order_id': f'eq.{order_id}', 'limit': 1})
        if pago is None:
            return
        _update('payments', int(pago['id']), cambios)
    else:
        pago = db.one('SELECT id FROM payments WHERE order_id = ? ORDER BY id LIMIT 1', (order_id,))
        if pago is None:
            return
        asignaciones = ','.join(f'{k}=?' for k in cambios)
        db.execute(f'UPDATE payments SET {asignaciones} WHERE id=?', (*cambios.values(), pago['id']))


def update_order_status(order_id: int, accion: str, referencia: str = '') -> dict[str, Any] | None:
    estados = {
        'aceptar': 'aceptada', 'rechazar': 'rechazada', 'preparando': 'preparando',
        'lista': 'lista', 'entregada': 'entregada', 'cancelar': 'cancelada',
    }
    if accion not in estados:
        raise ValueError('Acción de pedido no válida.')
    pedido = get_order(order_id)
    if pedido is None:
        return None
    nuevo = estados[accion]
    actual = pedido['estado']
    if actual in ('entregada', 'rechazada', 'cancelada') and nuevo != actual:
        raise ValueError('Ese pedido ya está cerrado.')
    ahora = now_local().isoformat(timespec='seconds')
    if supabase_enabled():
        _update('orders', order_id, {'status': nuevo, 'updated_at': ahora})
    else:
        db.execute('UPDATE orders SET status=?, updated_at=? WHERE id=?', (nuevo, ahora, order_id))
    if nuevo in ('rechazada', 'cancelada') and pedido['pago'] and pedido['pago']['estado'] == 'aprobado':
        _set_payment(order_id, 'reembolsado', referencia)
    return get_order(order_id)


def set_order_paid(order_id: int, pagado: bool, referencia: str = '',
                   admin_id: int | None = None) -> dict[str, Any] | None:
    """Solo el administrador puede confirmar un pago; el cliente nunca llega aquí."""
    pedido = get_order(order_id)
    if pedido is None:
        return None
    if pedido['estado'] in ('rechazada', 'cancelada'):
        raise ValueError('No se puede marcar como pagado un pedido rechazado o cancelado.')
    estado = (pedido['pago'] or {}).get('estado')
    if estado == 'reembolsado':
        raise ValueError('Ese pago fue reembolsado.')
    if pagado and estado == 'aprobado':
        raise ValueError('Este pago ya fue confirmado.')
    _set_payment(order_id, 'aprobado' if pagado else 'pendiente', referencia, admin_id)
    actualizar = now_local().isoformat(timespec='seconds')
    if supabase_enabled():
        _update('orders', order_id, {'updated_at': actualizar})
    else:
        db.execute('UPDATE orders SET updated_at=? WHERE id=?', (actualizar, order_id))
    return get_order(order_id)


def mark_proof_received(order_id: int) -> dict[str, Any] | None:
    """Subir una imagen nunca confirma el pago: solo pasa a «comprobante recibido»."""
    pedido = get_order(order_id)
    if pedido is None:
        return None
    if pedido['estado'] in ESTADOS_SIN_VENTA:
        raise ValueError('Ese pedido ya está cerrado.')
    estado = (pedido['pago'] or {}).get('estado', 'pendiente')
    if estado not in ESTADOS_CON_COMPROBANTE:
        raise ValueError('Ese pago ya fue confirmado o no admite comprobante.')
    _set_payment(order_id, 'comprobante_recibido')
    actualizar = now_local().isoformat(timespec='seconds')
    if supabase_enabled():
        _update('orders', order_id, {'updated_at': actualizar})
    else:
        db.execute('UPDATE orders SET updated_at=? WHERE id=?', (actualizar, order_id))
    return get_order(order_id)


def review_payment(order_id: int, decision: str, admin_id: int, nota: str = '',
                   referencia: str = '') -> dict[str, Any] | None:
    """El administrador verifica el dinero antes de aprobar una transferencia."""
    if decision not in ('en_verificacion', 'aprobado', 'rechazado'):
        raise ValueError('Decisión de pago no válida.')
    pedido = get_order(order_id)
    if pedido is None:
        return None
    if pedido['estado'] in ESTADOS_SIN_VENTA:
        raise ValueError('Ese pedido ya está cerrado.')
    pago = pedido['pago'] or {}
    if pago.get('estado') == 'reembolsado':
        raise ValueError('Ese pago fue reembolsado.')
    if decision == 'aprobado' and pago.get('estado') == 'aprobado':
        raise ValueError('Este pago ya fue confirmado.')
    if pago.get('metodo') == 'transferencia' and decision in ('en_verificacion', 'aprobado'):
        if get_payment_proof(order_id) is None:
            raise ValueError('El cliente aún no envió el comprobante.')
    _set_payment(order_id, decision, referencia, admin_id, nota)
    actualizar = now_local().isoformat(timespec='seconds')
    if supabase_enabled():
        _update('orders', order_id, {'updated_at': actualizar})
    else:
        db.execute('UPDATE orders SET updated_at=? WHERE id=?', (actualizar, order_id))
    return get_order(order_id)

