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


def allow_login(client_hash: str) -> bool:
    now = time.time()
    cutoff = now - 3600
    if supabase_enabled():
        return bool(_rpc("pv_allow_event", {
            "p_scope": "login_ip", "p_key_hash": client_hash,
            "p_created_epoch": now, "p_max": 10, "p_cutoff": cutoff,
        }))
    conn = db.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rate_events WHERE created_epoch < ?", (cutoff - 3600,))
        attempts = conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE scope='login_ip' AND key_hash=? AND created_epoch>?",
            (client_hash, cutoff),
        ).fetchone()[0]
        if attempts >= 10:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO rate_events(scope, key_hash, created_epoch) VALUES('login_ip',?,?)",
            (client_hash, now),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
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
        sql += " AND (full_name LIKE ? OR phone LIKE ?)"
        digits = re.sub(r"\D", "", q)
        params.extend([_like(q), _like(digits)])
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
