#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.jwt_auth import (
    create_admin_user,
    hash_password,
    _load_users,
    _save_users,
)


def cmd_create(args: list[str]) -> None:
    if len(args) < 2:
        print("Uso: python manage_admin.py create <username> <password> [nombre_completo]")
        sys.exit(1)

    username  = args[0]
    password  = args[1]
    full_name = " ".join(args[2:]) if len(args) > 2 else ""

    if len(password) < 8:
        print("Error: La contraseña debe tener al menos 8 caracteres.")
        sys.exit(1)

    try:
        user = create_admin_user(username, password, full_name)
        print(f"Usuario admin creado correctamente:")
        print(f"Username  : {user.username}")
        print(f"Nombre    : {user.full_name or '(sin nombre)'}")
        print(f"Disabled  : {user.disabled}")
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_list(_: list[str]) -> None:
    users = _load_users()
    if not users:
        print("No hay usuarios admin registrados.")
        return
    print(f"\n{'USERNAME':<20} {'NOMBRE':<30} {'ESTADO'}")
    print("-" * 60)
    for u in users.values():
        estado = "DESHABILITADO" if u.disabled else "Activo"
        print(f"{u.username:<20} {u.full_name or '':<30} {estado}")
    print()


def cmd_disable(args: list[str]) -> None:
    if not args:
        print("Uso: python manage_admin.py disable <username>")
        sys.exit(1)
    username = args[0]
    users = _load_users()
    if username not in users:
        print(f"Usuario '{username}' no encontrado.")
        sys.exit(1)
    users[username].disabled = True
    _save_users(users)
    print(f"Usuario '{username}' deshabilitado.")


def cmd_reset_password(args: list[str]) -> None:
    if len(args) < 2:
        print("Uso: python manage_admin.py reset-password <username> <nueva_password>")
        sys.exit(1)
    username, new_password = args[0], args[1]
    if len(new_password) < 8:
        print("La nueva contraseña debe tener al menos 8 caracteres.")
        sys.exit(1)
    users = _load_users()
    if username not in users:
        print(f"Usuario '{username}' no encontrado.")
        sys.exit(1)
    users[username].hashed_password = hash_password(new_password)
    _save_users(users)
    print(f"Contraseña de '{username}' restablecida correctamente.")


COMMANDS = {
    "create":         cmd_create,
    "list":           cmd_list,
    "disable":        cmd_disable,
    "reset-password": cmd_reset_password,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("Comandos disponibles:", ", ".join(COMMANDS.keys()))
        sys.exit(1)

    command = sys.argv[1]
    rest    = sys.argv[2:]
    COMMANDS[command](rest)
