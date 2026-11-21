"""Capa de persistencia SQLite (WAL, transacciones atómicas y restricciones).

Para producción con Supabase/PostgreSQL se incluye el esquema y la RPC en
``punto_vivo/database/supabase_schema.sql``.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .config import DB_PATH, ensure_dirs, now_local

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name  TEXT    NOT NULL,
    phone      TEXT    NOT NULL UNIQUE CHECK(length(phone) = 10),
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS visits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   INTEGER NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    visit_number  INTEGER NOT NULL CHECK(visit_number > 0),
    full_name     TEXT    NOT NULL,
    phone         TEXT    NOT NULL CHECK(length(phone) = 10),
    visit_date    TEXT    NOT NULL,
    visit_time    TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    created_epoch REAL    NOT NULL,
    request_token TEXT    NOT NULL UNIQUE,
    ip_hash       TEXT,
    UNIQUE(customer_id, visit_number)
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL DEFAULT '',
    image       TEXT    NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    price       NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK(price >= 0),
    image       TEXT    NOT NULL DEFAULT '',
    available   INTEGER NOT NULL DEFAULT 1 CHECK(available IN (0, 1)),
    is_demo     INTEGER NOT NULL DEFAULT 1 CHECK(is_demo IN (0, 1)),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'admin',
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT    PRIMARY KEY,
    admin_id   INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    csrf_token TEXT    NOT NULL,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS rate_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scope        TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    created_epoch REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE INDEX IF NOT EXISTS idx_visits_created   ON visits(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_visits_customer  ON visits(customer_id, visit_number);
CREATE INDEX IF NOT EXISTS idx_visits_phone     ON visits(phone);
CREATE INDEX IF NOT EXISTS idx_products_cat     ON products(category_id, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_rate_events      ON rate_events(scope, key_hash, created_epoch);
"""

DEMO_CATEGORIES = [
    ("Hamburguesas", "Demo · sustituir por la descripción real", 0),
    ("Sándwiches", "Demo · sustituir por la descripción real", 1),
    ("Papas fritas y acompañantes", "Demo · sustituir por la descripción real", 2),
    ("Jugos naturales", "Demo · sustituir por la descripción real", 3),
    ("Batidas", "Demo · sustituir por la descripción real", 4),
    ("Ensaladas", "Demo · sustituir por la descripción real", 5),
]

# Datos de demostración: NO son información comercial real. Precio 0 = pendiente.
DEMO_PRODUCTS: dict[str, list[tuple[str, str]]] = {
    "Hamburguesas": [
        ("Hamburguesa (DEMO)", "https://images.unsplash.com/photo-1568901346375-23c9450c58cd?auto=format&fit=crop&w=900&q=80"),
        ("Hamburguesa doble (DEMO)", "https://images.unsplash.com/photo-1550547660-d9450f859349?auto=format&fit=crop&w=900&q=80"),
    ],
    "Sándwiches": [
        ("Sándwich (DEMO)", "https://images.unsplash.com/photo-1553909489-cd47e0ef937f?auto=format&fit=crop&w=900&q=80"),
        ("Sándwich de pollo (DEMO)", "https://images.unsplash.com/photo-1539254664439-d255a1695b98?auto=format&fit=crop&w=900&q=80"),
    ],
    "Papas fritas y acompañantes": [
        ("Papas fritas (DEMO)", "https://images.unsplash.com/photo-1573080496219-bb080dd4f877?auto=format&fit=crop&w=900&q=80"),
        ("Papas con queso (DEMO)", "https://images.unsplash.com/photo-1630380464108-3e7e8a4fa6cd?auto=format&fit=crop&w=900&q=80"),
    ],
    "Jugos naturales": [
        ("Jugo natural (DEMO)", "https://images.unsplash.com/photo-1600271886742-f049cd451bba?auto=format&fit=crop&w=900&q=80"),
        ("Jugo de naranja (DEMO)", "https://images.unsplash.com/photo-1617531073652-0a682541ee28?auto=format&fit=crop&w=900&q=80"),
    ],
    "Batidas": [
        ("Batida (DEMO)", "https://images.unsplash.com/photo-1553530666-ba11a7da3888?auto=format&fit=crop&w=900&q=80"),
        ("Batida de frutas (DEMO)", "https://images.unsplash.com/photo-1505254197360-1c1e1f2d0d2b?auto=format&fit=crop&w=900&q=80"),
    ],
    "Ensaladas": [
        ("Ensalada (DEMO)", "https://images.unsplash.com/photo-1512621776951-a57141f2eefd?auto=format&fit=crop&w=900&q=80"),
        ("Ensalada fresca (DEMO)", "https://images.unsplash.com/photo-1546069901-ba9599a7e63c?auto=format&fit=crop&w=900&q=80"),
    ],
}

DEMO_DESCRIPTION = "Producto de demostración. Datos y precio pendientes de carga por el administrador."


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            for name, description, order in DEMO_CATEGORIES:
                conn.execute(
                    "INSERT INTO categories(name, description, sort_order, active) VALUES(?,?,?,1)",
                    (name, description, order),
                )
            for cat_name, items in DEMO_PRODUCTS.items():
                cat_id = conn.execute(
                    "SELECT id FROM categories WHERE name = ?", (cat_name,)
                ).fetchone()[0]
                for order, (name, image) in enumerate(items):
                    now = now_local().isoformat(timespec="seconds")
                    conn.execute(
                        """INSERT INTO products(category_id, name, description, price, image,
                                                available, is_demo, sort_order, created_at, updated_at)
                           VALUES(?,?,?,0,?,1,1,?,?,?)""",
                        (cat_id, name, DEMO_DESCRIPTION, image, order, now, now),
                    )
    finally:
        conn.close()


def one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def all_rows(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    conn = connect()
    try:
        cur = conn.execute(sql, params)
        return cur.lastrowid or 0
    finally:
        conn.close()
