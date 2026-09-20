from fastapi import APIRouter, Header, HTTPException, status
from app.schemas.auth import RegisterRequest, LoginRequest, UserResponse, TokenResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest) -> TokenResponse:
    """Registra un nuevo usuario con credenciales pre-hasheadas desde frontend."""
    return AuthService.register(req)


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(req: LoginRequest) -> TokenResponse:
    """Autentica a un usuario y genera su token de sesión JWT."""
    return AuthService.login(req)


@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def get_me(authorization: str = Header(None)) -> UserResponse:
    """Devuelve el perfil del usuario autenticado a partir del header Authorization."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta header de autorización Bearer token.",
        )
    token = authorization.split("Bearer ")[1].strip()
    return AuthService.get_current_user_from_token(token)
