import datetime
import time
import uuid
from typing import Optional
from fastapi import HTTPException, status
from app.core.db import get_user_by_email, get_user_by_id, create_user
from app.core.security import (
    hash_password,
    verify_password,
    create_jwt_token,
    decode_jwt_token,
)
from app.schemas.auth import RegisterRequest, LoginRequest, UserResponse, TokenResponse


class AuthService:
    @staticmethod
    def register(req: RegisterRequest) -> TokenResponse:
        email = req.email.strip().lower()
        if get_user_by_email(email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo ya se encuentra registrado.",
            )

        user_id = f"usr-{uuid.uuid4().hex[:8]}"
        user_record = create_user(
            user_id=user_id,
            email=email,
            name=req.name or "Chef de Cocina",
            password_hash=hash_password(req.password),
        )

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
        user_record = get_user_by_email(email)

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
        if not payload or not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o expirado.",
            )

        user_record = None
        user_id = payload.get("sub")
        if user_id:
            user_record = get_user_by_id(user_id)

        if not user_record and "email" in payload:
            user_record = get_user_by_email(payload["email"])

        if not user_record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Usuario no encontrado o sesión no válida.",
            )

        return UserResponse(
            id=user_record["id"],
            email=user_record["email"],
            name=user_record["name"],
        )
