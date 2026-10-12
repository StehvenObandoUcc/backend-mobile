from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool
from app.core.rate_limit import check_auth_rate_limit, get_client_ip
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    UserResponse,
    TokenResponse,
    DeleteAccountRequest,
    VerifyEmailRequest,
    ResendCodeRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    VerificationRequiredResponse,
    MessageResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _http(request: Request):
    """Cliente HTTP compartido de la app (pool y keep-alive) si existe; si no, supabase_auth crea uno propio."""
    return getattr(request.app.state, "http_client", None)



@router.post(
    "/register",
    response_model=TokenResponse | VerificationRequiredResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(req: RegisterRequest, request: Request, response: Response):
    """Registra un nuevo usuario. Modo local: 201 con token. Modo supabase: 202 y código por correo."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"reg_{client_ip}")
    result = await AuthService.register_flow(req, _http(request))
    if isinstance(result, VerificationRequiredResponse):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(req: LoginRequest, request: Request) -> TokenResponse:
    """Autentica a un usuario ejecutando PBKDF2 en threadpool con protección de rate limit."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"login_{client_ip}")
    return await AuthService.login_flow(req, _http(request))


@router.post("/verify-email", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def verify_email(req: VerifyEmailRequest, request: Request) -> TokenResponse:
    """Verifica el correo con el código de 6 dígitos y entrega el token (solo AUTH_PROVIDER=supabase)."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"verify_{client_ip}")
    return await AuthService.verify_email(req, _http(request))


@router.post("/resend-code", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def resend_code(req: ResendCodeRequest, request: Request) -> MessageResponse:
    """Reenvía el código de verificación sin revelar si la cuenta existe (solo AUTH_PROVIDER=supabase)."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"resend_{client_ip}")
    return await AuthService.resend_code(req, _http(request))


@router.post("/forgot-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def forgot_password(req: ForgotPasswordRequest, request: Request) -> MessageResponse:
    """Envía un código para crear una contraseña nueva sin revelar si el correo existe (solo supabase)."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"forgot_{client_ip}")
    return await AuthService.forgot_password(req, _http(request))


@router.post("/reset-password", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def reset_password(req: ResetPasswordRequest, request: Request) -> TokenResponse:
    """Cambia la contraseña con el código recibido por correo y entrega el token (solo supabase)."""
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"reset_{client_ip}")
    return await AuthService.reset_password(req, _http(request))


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


@router.post("/delete-account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(req: DeleteAccountRequest, request: Request, authorization: str = Header(None)) -> Response:
    """Elimina para siempre la cuenta del usuario autenticado y todos sus datos.
    Pide la contraseña actual y comparte el límite de intentos del login."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Falta header de autorización Bearer token.",
        )
    client_ip = get_client_ip(request)
    await check_auth_rate_limit(f"delete_{client_ip}")
    token = authorization.split("Bearer ")[1].strip()
    await AuthService.delete_account_flow(token, req.password, _http(request))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
