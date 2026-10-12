import datetime
import logging
import secrets
import sqlite3
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, NoReturn, Optional
from fastapi import HTTPException, status
from starlette.concurrency import run_in_threadpool
from app.core.config import settings
from app.core.db import (
    get_user_by_email,
    get_user_by_id,
    get_user_by_auth_id,
    create_user,
    delete_user_account,
    set_user_auth_link,
    set_user_email_verified,
    set_user_password_hash,
)
from app.core.security import (
    hash_password,
    verify_password,
    create_jwt_token,
    decode_jwt_token,
)
from app.schemas.auth import (
    RegisterRequest,
    LoginRequest,
    UserResponse,
    TokenResponse,
    VerifyEmailRequest,
    ResendCodeRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
    VerificationRequiredResponse,
    MessageResponse,
)
from app.services import supabase_auth
from app.services.supabase_auth import SupabaseAuthError

logger = logging.getLogger(__name__)

# Marca de contraseña en usuarios de Supabase Auth: no es un hash válido, verify_password devuelve False
SUPABASE_PASSWORD_MARKER = "!supabase"
DEFAULT_NAME = "Chef de Cocina"

MSG_RESEND_GENERIC = "Si la cuenta existe y no está verificada, te enviamos un código."
MSG_FORGOT_GENERIC = "Si el correo está registrado, te enviamos un código."

# Cooldown en memoria por correo para reenvíos y recovery (evita spam de correos y abuso de cuota de GoTrue)
COOLDOWN_SECONDS = 60
_last_sent: Dict[str, float] = {}


def _in_cooldown(email: str) -> bool:
    sent_at = _last_sent.get(email)
    return sent_at is not None and time.monotonic() - sent_at < COOLDOWN_SECONDS


def _mark_sent(email: str) -> None:
    if len(_last_sent) > 10_000:  # poda simple para que el dict no crezca sin límite
        cutoff = time.monotonic() - COOLDOWN_SECONDS
        for key in [k for k, v in _last_sent.items() if v < cutoff]:
            del _last_sent[key]
    _last_sent[email] = time.monotonic()


def reset_auth_cooldowns() -> None:
    """Vacía los cooldowns (para pruebas)."""
    _last_sent.clear()


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _unavailable() -> HTTPException:
    return _api_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "auth_unavailable",
        "El servicio de cuentas no está disponible temporalmente. Intenta de nuevo en unos segundos.",
    )


def _conflict() -> HTTPException:
    return _api_error(status.HTTP_409_CONFLICT, "account_conflict", "La cuenta presenta un conflicto. Contacta a soporte.")


def _raise_common(err: SupabaseAuthError) -> NoReturn:
    """Errores de Supabase Auth compartidos por todos los flujos nuevos."""
    if isinstance(err, supabase_auth.RateLimited):
        raise _api_error(
            status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited", "Espera un minuto antes de pedir otro código."
        )
    if isinstance(err, supabase_auth.WeakPassword):
        raise _api_error(
            status.HTTP_400_BAD_REQUEST, "weak_password", "La contraseña es muy débil. Usa una más larga y difícil de adivinar."
        )
    # AuthUnavailable y cualquier error de GoTrue no previsto
    raise _unavailable()


def _issue_token(user_record: Dict[str, Any]) -> TokenResponse:
    user_resp = UserResponse(
        id=user_record["id"],
        email=user_record["email"],
        name=user_record["name"],
    )
    token = create_jwt_token({"sub": user_record["id"], "email": user_record["email"]})
    expires_at = datetime.datetime.fromtimestamp(
        int(time.time()) + 60 * 60 * 24 * 7, tz=datetime.timezone.utc
    ).isoformat()
    return TokenResponse(access_token=token, expires_at=expires_at, user=user_resp)


def _new_user_id() -> str:
    return f"usr-{uuid.uuid4().hex[:8]}"


def _is_managed(user: Dict[str, Any]) -> bool:
    """Cuenta gestionada por Supabase Auth: su contraseña ya no vive en nuestra base (marcador "!supabase")."""
    return user.get("password_hash") == SUPABASE_PASSWORD_MARKER


async def _db(fn: Callable[..., Any], *args: Any) -> Any:
    """Llama a la base en el threadpool traduciendo sus fallos a errores de la API de auth."""
    try:
        return await run_in_threadpool(fn, *args)
    except HTTPException as err:
        if err.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
            raise _unavailable()
        raise
    except sqlite3.IntegrityError:
        logger.warning("[Auth] Violación de unicidad al crear o enlazar la cuenta.")
        raise _conflict()
    except Exception as err:
        text = str(err).lower()
        if "23505" in text or "duplicate key" in text:
            logger.warning("[Auth] Violación de unicidad al crear o enlazar la cuenta.")
            raise _conflict()
        logger.warning(
            "[Auth] Error de base de datos en un flujo de Supabase Auth (%s). "
            "¿Se corrió migrations/03_supabase_auth.sql?",
            type(err).__name__,
        )
        raise _unavailable()


async def _send(email: str, call: Callable[[], Awaitable[None]]) -> None:
    """Envía un código (reenvío o recovery) respetando el cooldown por correo.
    Dentro del cooldown no se llama a GoTrue; un 429 de GoTrue tampoco es error para quien llama."""
    if _in_cooldown(email):
        return
    try:
        await call()
    except supabase_auth.RateLimited:
        pass
    _mark_sent(email)


async def _ensure_no_conflict(user: Optional[Dict[str, Any]], auth_id: str) -> None:
    """409 si la fila ya está enlazada a otro auth_id o si ese auth_id pertenece a otra fila."""
    if user and user.get("auth_id") and user["auth_id"] != auth_id:
        raise _conflict()
    owner = await _db(get_user_by_auth_id, auth_id)
    if owner and (not user or owner["id"] != user["id"]):
        raise _conflict()


async def _make_managed(user: Dict[str, Any], auth_id: Optional[str], verified: bool) -> None:
    """Deja la fila como cuenta gestionada: enlaza auth_id (si se da y falta), marca la contraseña y el correo."""
    if auth_id and not user.get("auth_id"):
        await _db(set_user_auth_link, user["id"], auth_id)
        user["auth_id"] = auth_id
    if not _is_managed(user):
        await _db(set_user_password_hash, user["id"], SUPABASE_PASSWORD_MARKER)
        user["password_hash"] = SUPABASE_PASSWORD_MARKER
    if user.get("email_verified") != verified:
        await _db(set_user_email_verified, user["id"], verified)
        user["email_verified"] = verified


async def _new_managed_user(email: str, auth_id: Optional[str], verified: bool) -> Dict[str, Any]:
    return await _db(
        lambda: create_user(
            user_id=_new_user_id(),
            email=email,
            name=DEFAULT_NAME,
            password_hash=SUPABASE_PASSWORD_MARKER,
            auth_id=auth_id,
            email_verified=verified,
        )
    )


async def _discard_unfinished_signup(user: Optional[Dict[str, Any]], auth_id: str, http) -> None:
    """Mejor esfuerzo: borra el usuario de Supabase Auth y la fila local pendiente (marcada, sin verificar)
    para que un /register posterior con ese correo vuelva a funcionar."""
    try:
        await supabase_auth.admin_delete_user(auth_id, http_client=http)
    except SupabaseAuthError as err:
        logger.warning("[Auth] No se pudo borrar el usuario pendiente en Supabase Auth (error_code=%s).", err.code)
    if user and _is_managed(user) and not user["email_verified"]:
        try:
            await _db(delete_user_account, user["id"])
        except HTTPException:
            logger.warning("[Auth] No se pudo borrar la fila local pendiente.")


class AuthService:
    # ─── Modo local (comportamiento original) ──────────────────────────────────
    @staticmethod
    def register(req: RegisterRequest) -> TokenResponse:
        email = req.email.strip().lower()
        if get_user_by_email(email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo ya se encuentra registrado.",
            )

        user_id = _new_user_id()
        user_record = create_user(
            user_id=user_id,
            email=email,
            name=req.name or DEFAULT_NAME,
            password_hash=hash_password(req.password),
            email_verified=True,
        )
        return _issue_token(user_record)

    @staticmethod
    def login(req: LoginRequest) -> TokenResponse:
        email = req.email.strip().lower()
        user_record = get_user_by_email(email)

        if not user_record or not verify_password(req.password, user_record["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credenciales incorrectas. Verifica tu correo y contraseña.",
            )
        return _issue_token(user_record)

    @staticmethod
    def delete_account(token: str, password: str) -> None:
        """Elimina la cuenta del dueño del token si la contraseña es correcta (irreversible)."""
        current = AuthService.get_current_user_from_token(token)
        user_record = get_user_by_id(current.id)
        if not user_record or not verify_password(password, user_record["password_hash"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="La contraseña no es correcta.",
            )
        delete_user_account(current.id)

    @staticmethod
    def get_current_user_from_token(token: str) -> UserResponse:
        payload = decode_jwt_token(token)
        if not payload or not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o expirado.",
            )

        # Solo se confía en el claim `sub`: buscar por correo permitiría que el token de una cuenta
        # borrada entrara en otra cuenta nueva creada con el mismo correo.
        user_record = None
        user_id = payload.get("sub")
        if user_id:
            user_record = get_user_by_id(user_id)

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

    # ─── Despachadores según AUTH_PROVIDER ─────────────────────────────────────
    @staticmethod
    def is_supabase() -> bool:
        return settings.AUTH_PROVIDER == "supabase"

    @staticmethod
    async def register_flow(req: RegisterRequest, http=None):
        if AuthService.is_supabase():
            return await AuthService.register_supabase(req, http)
        return await run_in_threadpool(AuthService.register, req)

    @staticmethod
    async def login_flow(req: LoginRequest, http=None) -> TokenResponse:
        if AuthService.is_supabase():
            return await AuthService.login_supabase(req, http)
        return await run_in_threadpool(AuthService.login, req)

    @staticmethod
    async def delete_account_flow(token: str, password: str, http=None) -> None:
        if not AuthService.is_supabase():
            return await run_in_threadpool(AuthService.delete_account, token, password)
        await AuthService.delete_account_supabase(token, password, http)

    @staticmethod
    def require_supabase() -> None:
        if not AuthService.is_supabase():
            raise _api_error(status.HTTP_404_NOT_FOUND, "not_available", "Esta función no está disponible.")

    # ─── Modo supabase ─────────────────────────────────────────────────────────
    @staticmethod
    async def register_supabase(req: RegisterRequest, http=None) -> VerificationRequiredResponse:
        email = req.email.strip().lower()
        name = req.name or DEFAULT_NAME
        if len(req.password) < 8:
            raise _api_error(
                status.HTTP_400_BAD_REQUEST, "weak_password", "La contraseña debe tener al menos 8 caracteres."
            )

        existing = await _db(get_user_by_email, email)
        try:
            if existing:
                # Verificada, o cuenta antigua con contraseña propia (debe iniciar sesión para migrar)
                if existing["email_verified"] or not _is_managed(existing):
                    raise _api_error(
                        status.HTTP_400_BAD_REQUEST, "email_taken", "El correo ya se encuentra registrado."
                    )
                await _send(email, lambda: supabase_auth.resend_signup(email, http_client=http))
            else:
                # El id que devuelve sign_up NO es fiable (anti-enumeración de Supabase): no se guarda;
                # el auth_id se enlaza al verificar el código.
                try:
                    await supabase_auth.sign_up(email, req.password, name, http_client=http)
                    _mark_sent(email)
                except supabase_auth.AlreadyRegistered:
                    await _send(email, lambda: supabase_auth.resend_signup(email, http_client=http))
                await _db(
                    lambda: create_user(
                        user_id=_new_user_id(),
                        email=email,
                        name=name,
                        password_hash=SUPABASE_PASSWORD_MARKER,
                        auth_id=None,
                        email_verified=False,
                    )
                )
        except SupabaseAuthError as err:
            _raise_common(err)
        return VerificationRequiredResponse(email=email)

    @staticmethod
    async def verify_email(req: VerifyEmailRequest, http=None) -> TokenResponse:
        AuthService.require_supabase()
        try:
            auth_id, access_token = await supabase_auth.verify_otp(req.email, req.code, "signup", http_client=http)
        except supabase_auth.InvalidOtp:
            raise _api_error(status.HTTP_400_BAD_REQUEST, "invalid_code", "Código incorrecto o vencido.")
        except SupabaseAuthError as err:
            _raise_common(err)

        user = await _db(get_user_by_email, req.email)
        await _ensure_no_conflict(user, auth_id)
        # La contraseña final es la de quien demostró poseer el correo, no la de quien lo registró
        try:
            await supabase_auth.update_password(access_token, req.password, http_client=http)
        except supabase_auth.SamePassword:
            pass  # ya es esa contraseña
        except SupabaseAuthError as err:
            # El correo quedó confirmado pero la contraseña pudo ser la de un atacante: se descarta la cuenta
            logger.warning("[Auth] update_password falló tras verificar (error_code=%s); se descarta la cuenta.", err.code)
            await _discard_unfinished_signup(user, auth_id, http)
            raise _api_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "auth_unavailable",
                "No pudimos terminar la verificación. Regístrate de nuevo.",
            )

        if user is None:
            user = await _new_managed_user(req.email, auth_id, True)
        else:
            await _make_managed(user, auth_id, True)
        _last_sent.pop(req.email, None)
        return _issue_token(user)

    @staticmethod
    async def resend_code(req: ResendCodeRequest, http=None) -> MessageResponse:
        AuthService.require_supabase()
        user = await _db(get_user_by_email, req.email)
        if user and _is_managed(user) and not user["email_verified"]:
            try:
                await _send(req.email, lambda: supabase_auth.resend_signup(req.email, http_client=http))
            except SupabaseAuthError as err:
                logger.warning("[Auth] resend-code falló (error_code=%s).", err.code)
        return MessageResponse(message=MSG_RESEND_GENERIC)

    @staticmethod
    async def login_supabase(req: LoginRequest, http=None) -> TokenResponse:
        email = req.email.strip().lower()
        invalid = _api_error(
            status.HTTP_401_UNAUTHORIZED, "invalid_credentials", "Credenciales incorrectas. Verifica tu correo y contraseña."
        )
        not_verified = _api_error(
            status.HTTP_403_FORBIDDEN, "email_not_verified", "Verifica tu correo con el código que te enviamos."
        )
        user = await _db(get_user_by_email, email)
        if not user:
            raise invalid

        try:
            if _is_managed(user):
                try:
                    auth_id = await supabase_auth.sign_in(email, req.password, http_client=http)
                except supabase_auth.InvalidCredentials:
                    raise invalid
                except supabase_auth.EmailNotConfirmed:
                    await _send(email, lambda: supabase_auth.resend_signup(email, http_client=http))
                    raise not_verified
                await _ensure_no_conflict(user, auth_id)
                await _make_managed(user, auth_id, True)
                return _issue_token(user)

            # Cuenta antigua (contraseña PBKDF2 propia): se valida localmente y se migra a Supabase Auth
            valid = await run_in_threadpool(verify_password, req.password, user["password_hash"])
            if not valid:
                raise invalid
            try:
                # El id de sign_up no es fiable: no se guarda; se enlaza al verificar el código
                await supabase_auth.sign_up(email, req.password, user["name"], http_client=http)
            except (supabase_auth.WeakPassword, supabase_auth.AlreadyRegistered):
                admin_id: Optional[str] = None
                try:
                    admin_id = await supabase_auth.admin_create_user(
                        email, secrets.token_urlsafe(32), True, http_client=http
                    )
                except supabase_auth.AlreadyRegistered:
                    pass  # ya existe en Supabase: se continúa sin enlazar
                if admin_id:
                    await _ensure_no_conflict(user, admin_id)  # este id sí es fiable
                await _make_managed(user, admin_id, False)
                await _send(email, lambda: supabase_auth.send_recovery(email, http_client=http))
                raise _api_error(
                    status.HTTP_403_FORBIDDEN,
                    "password_reset_required",
                    "Por seguridad, crea una contraseña nueva: te enviamos un código a tu correo.",
                )
            _mark_sent(email)  # sign_up ya envió el código de verificación
            await _make_managed(user, None, False)
            raise not_verified
        except SupabaseAuthError as err:
            _raise_common(err)

    @staticmethod
    async def forgot_password(req: ForgotPasswordRequest, http=None) -> MessageResponse:
        AuthService.require_supabase()
        user = await _db(get_user_by_email, req.email)
        if user and not _in_cooldown(req.email):
            try:
                if not _is_managed(user):
                    # Cuenta antigua: se crea (confirmada) en Supabase SIN enlazar ni tocar la contraseña
                    # antigua; el enlace y el cambio de contraseña ocurren en /reset-password.
                    try:
                        await supabase_auth.admin_create_user(
                            req.email, secrets.token_urlsafe(32), True, http_client=http
                        )
                    except supabase_auth.AlreadyRegistered:
                        pass
                await _send(req.email, lambda: supabase_auth.send_recovery(req.email, http_client=http))
            except SupabaseAuthError as err:
                # Misma respuesta que si el correo no existiera: no se revela nada
                logger.warning("[Auth] forgot-password falló (error_code=%s).", err.code)
        return MessageResponse(message=MSG_FORGOT_GENERIC)

    @staticmethod
    async def reset_password(req: ResetPasswordRequest, http=None) -> TokenResponse:
        AuthService.require_supabase()
        try:
            auth_id, access_token = await supabase_auth.verify_otp(req.email, req.code, "recovery", http_client=http)
        except supabase_auth.InvalidOtp:
            raise _api_error(status.HTTP_400_BAD_REQUEST, "invalid_code", "Código incorrecto o vencido.")
        except SupabaseAuthError as err:
            _raise_common(err)

        user = await _db(get_user_by_email, req.email)
        await _ensure_no_conflict(user, auth_id)  # antes de cambiar la contraseña
        try:
            await supabase_auth.update_password(access_token, req.new_password, http_client=http)
        except supabase_auth.SamePassword:
            raise _api_error(
                status.HTTP_400_BAD_REQUEST, "same_password", "La nueva contraseña debe ser distinta de la anterior."
            )
        except SupabaseAuthError as err:
            _raise_common(err)

        if user is None:
            user = await _new_managed_user(req.email, auth_id, True)
        else:
            await _make_managed(user, auth_id, True)
        _last_sent.pop(req.email, None)
        return _issue_token(user)

    @staticmethod
    async def delete_account_supabase(token: str, password: str, http=None) -> None:
        current = await run_in_threadpool(AuthService.get_current_user_from_token, token)
        user = await _db(get_user_by_id, current.id)
        wrong = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La contraseña no es correcta.",
        )
        if not user:
            raise wrong
        if not _is_managed(user):
            # Cuenta antigua: contraseña PBKDF2 como siempre
            await run_in_threadpool(AuthService.delete_account, token, password)
            return
        try:
            signed_id = await supabase_auth.sign_in(user["email"], password, http_client=http)
        except (supabase_auth.InvalidCredentials, supabase_auth.EmailNotConfirmed):
            raise wrong
        except SupabaseAuthError as err:
            _raise_common(err)
        if user["auth_id"] and user["auth_id"] != signed_id:
            raise _conflict()

        # Primero Supabase Auth: si falla no se borra nada local (el usuario puede reintentar)
        try:
            await supabase_auth.admin_delete_user(user["auth_id"] or signed_id, http_client=http)
        except SupabaseAuthError as err:
            logger.warning("[Auth] No se pudo borrar el usuario en Supabase Auth (error_code=%s).", err.code)
            raise _unavailable()
        await _db(delete_user_account, user["id"])
