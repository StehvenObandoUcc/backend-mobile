from fastapi import APIRouter, Header, HTTPException, Request, status
from starlette.concurrency import run_in_threadpool
from app.core.rate_limit import check_auth_rate_limit, get_client_ip
from app.schemas.auth import RegisterRequest, LoginRequest, UserResponse, TokenResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])



@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, request: Request) -> TokenResponse:
    """Registra un nuevo usuario ejecutando el cómputo de hashing en threadpool."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"reg_{client_ip}")
    return await run_in_threadpool(AuthService.register, req)


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(req: LoginRequest, request: Request) -> TokenResponse:
    """Autentica a un usuario ejecutando PBKDF2 en threadpool con protección de rate limit."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"login_{client_ip}")
    return await run_in_threadpool(AuthService.login, req)


@router.get("/me", response_model=UserResponse, status_code=status.HTTP_200_OK)
async def get_me(authorization: str = Header(None)) -> UserResponse:
    """Devuelve el perfil del usuario autenticado a partir del header Authorization."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta header de autorización Bearer token.",
        )
    token = authorization.split("Bearer ")[1].strip()
    return await run_in_threadpool(AuthService.get_current_user_from_token, token)
