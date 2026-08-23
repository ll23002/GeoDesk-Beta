from __future__ import annotations

import logging
import os
from typing import Optional

import hyp3_sdk as sdk
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from utils.jwt_auth import (
    TokenData,
    authenticate_admin,
    create_access_token,
    create_admin_user,
    require_admin,
    get_current_user,
    _load_users,
)

load_dotenv()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


# Modelos de Petición / Respuesta
class AdminLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str
    expires_in_hours: int


class UserInfo(BaseModel):
    username: str
    role: str
    full_name: Optional[str] = None


class CreateUserRequest(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None


class AuthStatusResponse(BaseModel):
    has_hyp3: bool
    has_era5: bool
    hyp3_from_env: bool
    era5_from_env: bool


# Autenticación JWT

@router.post("/admin/login", response_model=TokenResponse)
def admin_login(body: AdminLoginRequest):
    """
    Inicio de sesión para investigadores / administradores de GeoDesk.

    Valida las credenciales contra el archivo de usuarios admin (admin_users.json)
    y retorna un token JWT válido por 12 horas (configurable en .env con JWT_EXPIRE_HOURS).

    El token debe enviarse en el header de peticiones protegidas:
        Authorization: Bearer <token>
    """
    if not body.username or not body.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Usuario y contraseña son obligatorios.",
        )

    user = authenticate_admin(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from utils.jwt_auth import ACCESS_TOKEN_EXPIRE_HOURS
    token = create_access_token(username=user.username, role="admin")

    logger.info("[Auth] Inicio de sesión exitoso: %s", user.username)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        username=user.username,
        role="admin",
        expires_in_hours=ACCESS_TOKEN_EXPIRE_HOURS,
    )


@router.post("/token", response_model=TokenResponse, include_in_schema=True)
def login_oauth2_form(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Alias OAuth2 compatible con la interfaz de Swagger UI (/docs).
    Usa el mismo backend que /admin/login.
    """
    user = authenticate_admin(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from utils.jwt_auth import ACCESS_TOKEN_EXPIRE_HOURS
    token = create_access_token(username=user.username, role="admin")

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        username=user.username,
        role="admin",
        expires_in_hours=ACCESS_TOKEN_EXPIRE_HOURS,
    )


@router.get("/me", response_model=UserInfo)
def get_me(current_user: TokenData = Depends(require_admin)):
    """
    Retorna la información del usuario actualmente autenticado.
    Requiere token JWT válido en el header Authorization: Bearer <token>.
    """
    users = _load_users()
    user = users.get(current_user.username)
    return UserInfo(
        username=current_user.username,
        role=current_user.role,
        full_name=user.full_name if user else None,
    )


# Gestión de Usuarios Admin

@router.post("/admin/users/create", status_code=201)
def create_user(
    body: CreateUserRequest,
    current_user: TokenData = Depends(require_admin),
):
    """
    Crea un nuevo usuario administrador en la plataforma.
    Solo un administrador autenticado puede crear otros administradores.

    Primero ejecutar el script de creación del usuario inicial:
        python manage_admin.py create <username> <password>
    """
    if len(body.password) < 8:
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener al menos 8 caracteres.",
        )
    try:
        user = create_admin_user(
            username=body.username,
            password=body.password,
            full_name=body.full_name or "",
        )
        logger.info(
            "[Auth] Usuario '%s' creado por admin '%s'",
            body.username, current_user.username,
        )
        return {"ok": True, "message": f"Usuario '{body.username}' creado correctamente."}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/admin/users")
def list_users(current_user: TokenData = Depends(require_admin)):
    """
    Lista todos los usuarios admin registrados.
    """
    users = _load_users()
    return {
        "ok": True,
        "users": [
            {
                "username": u.username,
                "full_name": u.full_name,
                "disabled": u.disabled,
            }
            for u in users.values()
        ],
    }


@router.delete("/admin/users/{username}")
def disable_user(
    username: str,
    current_user: TokenData = Depends(require_admin),
):
    """
    Deshabilita un usuario admin (no lo elimina del archivo para mantener
    el registro histórico). Un admin no puede deshabilitarse a sí mismo.
    """
    if username == current_user.username:
        raise HTTPException(
            status_code=400,
            detail="No puedes deshabilitar tu propio usuario.",
        )
    from utils.jwt_auth import _load_users, _save_users
    users = _load_users()
    if username not in users:
        raise HTTPException(status_code=404, detail=f"Usuario '{username}' no encontrado.")
    users[username].disabled = True
    _save_users(users)
    logger.info("[Auth] Usuario '%s' deshabilitado por '%s'", username, current_user.username)
    return {"ok": True, "message": f"Usuario '{username}' deshabilitado."}


# Estado de Credenciales (Compatibilidad)

@router.get("/status", response_model=AuthStatusResponse)
def auth_status():
    """
    Retorna si las credenciales de HyP3 y ERA5 están configuradas en el servidor.
    No requiere autenticación — útil para que la UI sepa si mostrar el formulario
    de credenciales o si el servidor ya las tiene configuradas en .env.
    """
    hyp3_user_env = os.getenv("HYP3_USERNAME")
    hyp3_pass_env = os.getenv("HYP3_PASSWORD")
    era5_key_env  = os.getenv("ERA5_KEY")

    return AuthStatusResponse(
        has_hyp3=bool(hyp3_user_env and hyp3_pass_env),
        has_era5=bool(era5_key_env),
        hyp3_from_env=bool(hyp3_user_env and hyp3_pass_env),
        era5_from_env=bool(era5_key_env),
    )
