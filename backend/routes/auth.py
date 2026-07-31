from __future__ import annotations

import os
from typing import Optional

import hyp3_sdk as sdk
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

load_dotenv()

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class LoginRequest(BaseModel):
    hyp3_username: str
    hyp3_password: str
    era5_key: Optional[str] = None


class LoginResponse(BaseModel):
    ok: bool
    message: str
    hyp3_username: str
    hyp3_quota: Optional[int] = None


class AuthStatusResponse(BaseModel):
    has_hyp3: bool
    has_era5: bool
    hyp3_from_env: bool
    era5_from_env: bool


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    if not body.hyp3_username or not body.hyp3_password:
        raise HTTPException(status_code=400, detail="Usuario y contraseña de HyP3 son obligatorios.")

    try:
        hyp3 = sdk.HyP3(username=body.hyp3_username, password=body.hyp3_password)
        user_info = hyp3.my_info()
        quota = None
        if user_info and hasattr(user_info, "quota"):
            quota = getattr(user_info.quota, "remaining", None)
    except Exception as e:
        err = str(e).lower()
        if "401" in err or "unauthorized" in err or "authentication" in err or "credentials" in err:
            raise HTTPException(
                status_code=401,
                detail="Credenciales de HyP3 incorrectas. Verifica tu usuario y contraseña."
            )
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo conectar al servidor HyP3: {e}"
        )

    return LoginResponse(
        ok=True,
        message=f"Autenticación exitosa como {body.hyp3_username}",
        hyp3_username=body.hyp3_username,
        hyp3_quota=quota,
    )


@router.get("/status", response_model=AuthStatusResponse)
def auth_status():
    hyp3_user_env = os.getenv("HYP3_USERNAME")
    hyp3_pass_env = os.getenv("HYP3_PASSWORD")
    era5_key_env  = os.getenv("ERA5_KEY")

    return AuthStatusResponse(
        has_hyp3=bool(hyp3_user_env and hyp3_pass_env),
        has_era5=bool(era5_key_env),
        hyp3_from_env=bool(hyp3_user_env and hyp3_pass_env),
        era5_from_env=bool(era5_key_env),
    )
