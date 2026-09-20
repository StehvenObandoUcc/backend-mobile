from typing import Optional
from pydantic import BaseModel, EmailStr, Field

class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, description="Correo electrónico del usuario")
    password: str = Field(min_length=6, description="Contraseña del usuario en texto plano sobre TLS")
    name: Optional[str] = Field(default="Chef de Cocina", description="Nombre del usuario")

class LoginRequest(BaseModel):
    email: str = Field(min_length=3, description="Correo electrónico")
    password: str = Field(min_length=6, description="Contraseña del usuario")

class UserResponse(BaseModel):
    id: str
    email: str
    name: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str
    user: UserResponse
