"""Cliente mínimo de Supabase Auth (GoTrue) por REST con httpx.

El backend actúa como intermediario: GoTrue es dueño de la contraseña y del correo verificado,
y el backend emite su propio JWT. Sin estado global de sesión y sin reintentos (un intento por llamada).
Nunca se registran claves, contraseñas, códigos ni tokens: solo códigos de estado y error_code.
"""
import logging
from urllib.parse import quote
from typing import Any, Dict, Optional, Tuple

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


# ─── Errores tipados ───────────────────────────────────────────────────────────

class SupabaseAuthError(Exception):
    """Error base de Supabase Auth. `code` es el error_code de GoTrue (o uno interno)."""

    def __init__(self, code: str = "unknown", status_code: Optional[int] = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class InvalidCredentials(SupabaseAuthError):
    pass


class EmailNotConfirmed(SupabaseAuthError):
    pass


class InvalidOtp(SupabaseAuthError):
    pass


class WeakPassword(SupabaseAuthError):
    pass


class SamePassword(SupabaseAuthError):
    pass


class AlreadyRegistered(SupabaseAuthError):
    pass


class RateLimited(SupabaseAuthError):
    pass


class AuthUnavailable(SupabaseAuthError):
    pass


# ─── Mapeo de respuestas de GoTrue ─────────────────────────────────────────────

def _extract(body: Any) -> Tuple[str, str]:
    """Devuelve (código, mensaje) en minúsculas desde ambos formatos de error de GoTrue."""
    if not isinstance(body, dict):
        return "", ""
    code = body.get("error_code") or ""
    if not code and isinstance(body.get("code"), str):
        code = body["code"]
    msg = body.get("msg") or body.get("error_description") or body.get("message") or ""
    if not msg and isinstance(body.get("error"), str):
        msg = body["error"]
    legacy = body.get("error") if isinstance(body.get("error"), str) else ""
    return str(code).lower(), f"{msg} {legacy}".strip().lower()


def _map_error(status_code: int, body: Any, path: str) -> SupabaseAuthError:
    code, msg = _extract(body)
    label = code or f"http_{status_code}"
    if status_code >= 500:
        return AuthUnavailable(label, status_code)
    if status_code == 429 or code in ("over_email_send_rate_limit", "over_request_rate_limit", "over_sms_send_rate_limit"):
        return RateLimited(label, status_code)
    if code == "email_not_confirmed" or "email not confirmed" in msg:
        return EmailNotConfirmed(label, status_code)
    if code == "same_password" or "different from the old password" in msg:
        return SamePassword(label, status_code)
    if code in ("user_already_exists", "email_exists") or "already registered" in msg or "already been registered" in msg:
        return AlreadyRegistered(label, status_code)
    if code == "weak_password" or (status_code == 422 and "password" in msg):
        return WeakPassword(label, status_code)
    if (
        code in ("otp_expired", "otp_disabled")
        or "token has expired or is invalid" in msg
        or "invalid otp" in msg
        or (status_code == 403 and path.startswith("/verify"))
    ):
        return InvalidOtp(label, status_code)
    if code == "invalid_credentials" or "invalid login credentials" in msg or (
        status_code == 400 and "invalid_grant" in msg
    ):
        return InvalidCredentials(label, status_code)
    return SupabaseAuthError(label, status_code)


# ─── Transporte ────────────────────────────────────────────────────────────────

def _base_url() -> str:
    return f"{settings.SUPABASE_URL}/auth/v1"


def _headers(admin: bool) -> Dict[str, str]:
    """Los endpoints /admin siempre usan la clave de servicio; el resto, SUPABASE_ANON_KEY si existe."""
    key = settings.SUPABASE_KEY if admin else (settings.SUPABASE_ANON_KEY or settings.SUPABASE_KEY)
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, str]] = None,
    headers: Optional[Dict[str, str]] = None,
    admin: bool = False,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Any:
    """Una sola llamada a GoTrue. Devuelve el JSON (dict) o lanza un error tipado."""
    url = f"{_base_url()}{path}"
    hdrs = headers if headers is not None else _headers(admin)
    timeout = settings.SUPABASE_AUTH_TIMEOUT_SECONDS
    try:
        if http_client is not None:
            resp = await http_client.request(method, url, json=json, params=params, headers=hdrs, timeout=timeout)
        else:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, json=json, params=params, headers=hdrs)
    except httpx.TimeoutException:
        logger.warning("[SupabaseAuth] %s %s: timeout", method, path)
        raise AuthUnavailable("timeout")
    except httpx.HTTPError as err:
        logger.warning("[SupabaseAuth] %s %s: error de red (%s)", method, path, type(err).__name__)
        raise AuthUnavailable("network_error")

    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        logger.warning("[SupabaseAuth] %s %s: respuesta no JSON (HTTP %d)", method, path, resp.status_code)
        raise AuthUnavailable("invalid_response", resp.status_code)

    if resp.status_code >= 400:
        err = _map_error(resp.status_code, body, path)
        logger.warning(
            "[SupabaseAuth] %s %s: HTTP %d error_code=%s", method, path, resp.status_code, err.code
        )
        raise err
    return body


def _user_id(body: Any) -> str:
    """Extrae el id del usuario de una respuesta de GoTrue (`id` o `user.id`)."""
    if isinstance(body, dict):
        if isinstance(body.get("user"), dict) and body["user"].get("id"):
            return str(body["user"]["id"])
        if body.get("id"):
            return str(body["id"])
    raise AuthUnavailable("invalid_response")


# ─── Operaciones ───────────────────────────────────────────────────────────────

async def sign_up(email: str, password: str, name: str, http_client: Optional[httpx.AsyncClient] = None) -> str:
    body = await _request(
        "POST", "/signup",
        json={"email": email, "password": password, "data": {"name": name}},
        http_client=http_client,
    )
    return _user_id(body)


async def sign_in(email: str, password: str, http_client: Optional[httpx.AsyncClient] = None) -> str:
    body = await _request(
        "POST", "/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        http_client=http_client,
    )
    return _user_id(body)


async def verify_otp(
    email: str, token: str, type: str, http_client: Optional[httpx.AsyncClient] = None
) -> Tuple[str, str]:
    """Verifica un código de 6 dígitos. Devuelve (auth_user_id, access_token)."""
    body = await _request(
        "POST", "/verify",
        json={"type": type, "email": email, "token": token},
        http_client=http_client,
    )
    access_token = body.get("access_token") if isinstance(body, dict) else None
    if not access_token:
        raise AuthUnavailable("invalid_response")
    return _user_id(body), str(access_token)


async def send_recovery(email: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
    await _request("POST", "/recover", json={"email": email}, http_client=http_client)


async def resend_signup(email: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
    await _request("POST", "/resend", json={"type": "signup", "email": email}, http_client=http_client)


async def update_password(access_token: str, new_password: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
    await _request(
        "PUT", "/user",
        json={"password": new_password},
        headers={
            "apikey": settings.SUPABASE_ANON_KEY or settings.SUPABASE_KEY,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        http_client=http_client,
    )


async def admin_create_user(
    email: str, password: str, email_confirm: bool, http_client: Optional[httpx.AsyncClient] = None
) -> str:
    body = await _request(
        "POST", "/admin/users",
        json={"email": email, "password": password, "email_confirm": email_confirm},
        admin=True,
        http_client=http_client,
    )
    return _user_id(body)


async def admin_delete_user(auth_id: str, http_client: Optional[httpx.AsyncClient] = None) -> None:
    try:
        await _request("DELETE", f"/admin/users/{quote(auth_id, safe='')}", admin=True, http_client=http_client)
    except SupabaseAuthError as err:
        if err.status_code == 404:
            return  # ya no existe en Supabase Auth: el objetivo del borrado está cumplido
        raise
