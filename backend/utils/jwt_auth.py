import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel

load_dotenv()

logger = logging.getLogger(__name__)

# Configuración
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))

_env_secret = os.getenv("JWT_SECRET_KEY", "")
if not _env_secret:
    logger.warning(
        "[JWT] JWT_SECRET_KEY no definida en .env — generando clave temporal. "
        "TODOS los tokens existentes se invalidarán en el próximo reinicio. "
        "Defina JWT_SECRET_KEY en .env para producción."
    )
    _env_secret = secrets.token_hex(32)

SECRET_KEY: str = _env_secret

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ADMIN_USERS_FILE = DATA_DIR / "admin_users.json"

# Esquema OAuth2 para Swagger UI y headers Authorization: Bearer <token>
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


# Modelos
class TokenData(BaseModel):
    username: str
    role: str  # "admin"


class AdminUser(BaseModel):
    username: str
    hashed_password: str
    full_name: Optional[str] = None
    disabled: bool = False


# Gestión del archivo de usuarios

def _load_users() -> dict[str, AdminUser]:
    """Carga los usuarios admin desde el archivo JSON en disco."""
    if not ADMIN_USERS_FILE.exists():
        return {}
    try:
        raw = json.loads(ADMIN_USERS_FILE.read_text(encoding="utf-8"))
        return {u: AdminUser(**data) for u, data in raw.items()}
    except Exception as exc:
        logger.error("[JWT] Error leyendo admin_users.json: %s", exc)
        return {}


def _save_users(users: dict[str, AdminUser]) -> None:
    """Guarda los usuarios admin en disco."""
    ADMIN_USERS_FILE.write_text(
        json.dumps(
            {u: user.model_dump() for u, user in users.items()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# Operaciones de contraseñas
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# Operaciones de tokens
def create_access_token(username: str, role: str = "admin") -> str:
    """
    Genera un token JWT firmado con la SECRET_KEY.
    El token incluye el username, el rol y la fecha de expiración.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[TokenData]:
    """
    Decodifica y valida un token JWT.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role", "public")
        if not username:
            return None
        return TokenData(username=username, role=role)
    except JWTError:
        return None


# Dependencias FastAPI

async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
) -> Optional[TokenData]:
    """
    Dependencia que extrae el usuario del token JWT si está presente.
    """
    if not token:
        return None
    return decode_token(token)


async def require_admin(
    token: Optional[str] = Depends(oauth2_scheme),
) -> TokenData:
    """
    Dependencia que EXIGE un token JWT admin válido.
    Lanza HTTP 401 si no hay token o es inválido.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Acceso denegado. Se requiere autenticación de administrador.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception

    token_data = decode_token(token)
    if token_data is None or token_data.role != "admin":
        raise credentials_exception

    # Verificar que el usuario sigue activo en el archivo de usuarios
    users = _load_users()
    user = users.get(token_data.username)
    if user is None or user.disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario deshabilitado o eliminado.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return token_data


# Funciones de administración de usuarios

def create_admin_user(username: str, password: str, full_name: str = "") -> AdminUser:
    """
    Crea un nuevo usuario admin y lo guarda en disco.
    Lanza ValueError si el usuario ya existe.
    """
    users = _load_users()
    if username in users:
        raise ValueError(f"El usuario '{username}' ya existe.")
    user = AdminUser(
        username=username,
        hashed_password=hash_password(password),
        full_name=full_name,
        disabled=False,
    )
    users[username] = user
    _save_users(users)
    logger.info("[JWT] Usuario admin creado: %s", username)
    return user


def authenticate_admin(username: str, password: str) -> Optional[AdminUser]:
    """
    Valida las credenciales de un usuario admin.
    Retorna el AdminUser si son correctas, None si no.
    """
    users = _load_users()
    user = users.get(username)
    if not user or user.disabled:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
