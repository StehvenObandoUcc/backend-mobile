import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')

class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=100, description="Correo electrónico del usuario")
    password: str = Field(min_length=6, max_length=128, description="Contraseña del usuario en texto plano sobre TLS")
    name: Optional[str] = Field(default="Chef de Cocina", min_length=2, max_length=50, description="Nombre del usuario")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_REGEX.match(v):
            raise ValueError("Formato de correo electrónico inválido.")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return "Chef de Cocina"
        v = v.strip()
        if len(v) < 2:
            raise ValueError("El nombre debe tener al menos 2 caracteres.")
        if len(v) > 50:
            raise ValueError("El nombre no puede exceder 50 caracteres.")
        return v

class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=100, description="Correo electrónico")
    password: str = Field(min_length=6, max_length=128, description="Contraseña del usuario")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not EMAIL_REGEX.match(v):
            raise ValueError("Formato de correo electrónico inválido.")
        return v

class UserResponse(BaseModel):
    id: str
    email: str
    name: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: UserResponse
