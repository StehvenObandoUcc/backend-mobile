import uuid
from typing import Dict, Optional
from fastapi import HTTPException, status
from app.core.security import (
    hash_password,
    verify_password,
    create_jwt_token,
    decode_jwt_token,
)
from app.schemas.auth import RegisterRequest, LoginRequest, UserResponse, TokenResponse

# Almacenamiento en memoria de usuarios (email -> dict con datos y password_hash)
USERS_DB: Dict[str, dict] = {}


import datetime
import time

def _init_demo_user():
    demo_email = "demo@foodai.com"
    if demo_email not in USERS_DB:
        # Clave demo: "123456" hasheada con PBKDF2 OWASP (600,000 iteraciones)
        USERS_DB[demo_email] = {
            "id": "usr-demo-1",
            "email": demo_email,
            "name": "Chef Demo",
            "password_hash": hash_password("123456"),
        }

_init_demo_user()


class AuthService:
    @staticmethod
    def register(req: RegisterRequest) -> TokenResponse:
        email = req.email.strip().lower()
        if email in USERS_DB:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo ya se encuentra registrado.",
            )

        user_id = f"usr-{uuid.uuid4().hex[:8]}"
        user_record = {
            "id": user_id,
            "email": email,
            "name": req.name or "Chef de Cocina",
            "password_hash": hash_password(req.password),
        }
        USERS_DB[email] = user_record

        user_resp = UserResponse(
            id=user_record["id"],
            email=user_record["email"],
            name=user_record["name"],
        )
        token = create_jwt_token({"sub": user_id, "email": email})
        expires_at = datetime.datetime.fromtimestamp(
            int(time.time()) + 60 * 60 * 24 * 7, tz=datetime.timezone.utc
        ).isoformat()

        return TokenResponse(access_token=token, expires_at=expires_at, user=user_resp)

    @staticmethod
    def login(req: LoginRequest) -> TokenResponse:
        email = req.email.strip().lower()
        user_record = USERS_DB.get(email)

        if not user_record or not verify_password(req.password, user_record["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales incorrectas. Verifica tu correo y contraseña.",
            )

        user_resp = UserResponse(
            id=user_record["id"],
            email=user_record["email"],
            name=user_record["name"],
        )
        token = create_jwt_token({"sub": user_record["id"], "email": email})
        expires_at = datetime.datetime.fromtimestamp(
            int(time.time()) + 60 * 60 * 24 * 7, tz=datetime.timezone.utc
        ).isoformat()

        return TokenResponse(access_token=token, expires_at=expires_at, user=user_resp)

    @staticmethod
    def get_current_user_from_token(token: str) -> UserResponse:
        payload = decode_jwt_token(token)
        if not payload or "email" not in payload:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o expirado.",
            )

        email = payload["email"]
        user_record = USERS_DB.get(email)
        if not user_record:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado.",
            )

        return UserResponse(
            id=user_record["id"],
            email=user_record["email"],
            name=user_record["name"],
        )
