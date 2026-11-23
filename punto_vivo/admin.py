"""Utilidad de línea de comandos para administradores (nunca desde el navegador).

    .venv\\Scripts\\python.exe -m punto_vivo.admin create --email admin@ejemplo.com
"""
from __future__ import annotations

import argparse
import getpass
import os
import re

from . import repository
from .config import now_local
from .security import hash_password

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def validar_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Correo electrónico inválido.")
    return email


def validar_password(password: str) -> None:
    if len(password) < 10:
        raise ValueError("La contraseña debe tener al menos 10 caracteres.")


def create_admin(email: str, password: str, role: str = "admin") -> int:
    email = validar_email(email)
    validar_password(password)
    if repository.get_admin_by_email(email):
        raise ValueError("Ya existe un administrador con ese correo.")
    return repository.create_admin(
        email, hash_password(password), role, now_local().isoformat(timespec="seconds")
    )


def change_password(email: str, password: str) -> None:
    validar_password(password)
    row = repository.get_admin_by_email(validar_email(email))
    if row is None:
        raise ValueError("No existe ese administrador.")
    repository.change_admin_password(row["id"], hash_password(password))
    repository.delete_sessions_by_admin(row["id"])


def bootstrap_from_env() -> None:
    """Crea el primer administrador si se configuró por variables de entorno."""
    email = os.getenv("PV_ADMIN_EMAIL", "").strip()
    password = os.getenv("PV_ADMIN_PASSWORD", "")
    if email and password:
        try:
            create_admin(email, password)
        except ValueError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Administradores de PUNTO VIVO FRESH")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Crear administrador")
    create.add_argument("--email", required=True)
    create.add_argument("--password", help="Si se omite se pide de forma segura")
    create.add_argument("--role", default="admin")
    reset = sub.add_parser("password", help="Cambiar contraseña")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password")
    sub.add_parser("list", help="Listar administradores")
    args = parser.parse_args()

    repository.init_db()
    try:
        if args.command == "create":
            password = args.password or getpass.getpass("Contraseña (mín. 10 caracteres): ")
            new_id = create_admin(args.email, password, args.role)
            print(f"Administrador creado con id {new_id}.")
        elif args.command == "password":
            password = args.password or getpass.getpass("Nueva contraseña (mín. 10 caracteres): ")
            change_password(args.email, password)
            print("Contraseña actualizada. Sesiones cerradas.")
        else:
            for row in repository.list_admins():
                print(f"{row['id']}  {row['email']}  {row['role']}  {row['created_at']}")
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
