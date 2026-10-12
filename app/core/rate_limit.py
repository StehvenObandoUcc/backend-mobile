import asyncio
import logging
from collections import deque
from typing import Optional
from time import monotonic
from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_calls = deque()

# Costo aproximado por llamada a DeepSeek Flash (con imagen y prompt ~600 tokens): ~$0.00015 USD
ESTIMATED_COST_PER_CALL_USD = 0.00015
_total_calls = 0

# NOTA DE PRODUCCIÓN (Multi-worker):
# Este rate limiter opera in-memory por proceso (instancia de worker).
# En entornos con múltiples workers de Uvicorn/Gunicorn en cluster, se recomienda
# conectar a Redis si se requiere un contador estrictamente unificado entre procesos.


def get_client_ip(request: Request) -> str:
    """Extrae la IP real del cliente detrás del router de Heroku.
    El router AÑADE la IP de conexión al final de X-Forwarded-For; los valores previos los controla
    el cliente, por lo que usar el primero permitiría evadir el rate limit rotando la cabecera."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


async def check_rate_limit(max_per_minute: int = 5):
    """Rate limiter global en memoria para llamadas a la IA (default 5 en fase de pruebas)."""
    global _total_calls
    async with _lock:
        now = monotonic()
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= max_per_minute:
            logger.warning("[RateLimit] Límite global de %d llamadas/min alcanzado.", max_per_minute)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Límite de prueba alcanzado ({max_per_minute} peticiones/minuto). Espera un momento antes de volver a intentar.",
                headers={"X-Error-Code": "rate_limited"},
            )
        _calls.append(now)
        _total_calls += 1
        logger.info(
            "[AI Cost Log] Llamada #%d registrada. Costo estimado acumulado: ~$%.5f USD.",
            _total_calls,
            _total_calls * ESTIMATED_COST_PER_CALL_USD,
        )


_auth_lock = asyncio.Lock()
_auth_attempts: dict[str, deque] = {}


async def check_auth_rate_limit(client_identifier: str, max_attempts_per_minute: int = 15):
    """Protege contra ataques de fuerza bruta y agotamiento de CPU en endpoints de autenticación."""
    async with _auth_lock:
        now = monotonic()

        # Poda oportunista para evitar fuga de memoria si hay muchas IPs efímeras/rotativas
        if len(_auth_attempts) > 200:
            expired_keys = [
                k for k, timestamps in _auth_attempts.items()
                if not timestamps or now - timestamps[-1] > 60
            ]
            for k in expired_keys:
                _auth_attempts.pop(k, None)

        if client_identifier not in _auth_attempts:
            _auth_attempts[client_identifier] = deque()
        attempts = _auth_attempts[client_identifier]
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        if len(attempts) >= max_attempts_per_minute:
            logger.warning("[AuthRateLimit] Límite de %d intentos alcanzado para %s.", max_attempts_per_minute, client_identifier)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiados intentos de autenticación. Por favor espera un momento antes de reintentar.",
            )
        attempts.append(now)


_ai_client_lock = asyncio.Lock()
_ai_client_calls: dict[str, deque] = {}


async def check_ai_rate_limit(client_identifier: str, max_per_minute: int = 5):
    """Protege contra abuso de la API de IA por cliente/IP individual y limita a 5 fotos/minuto en pruebas."""
    async with _ai_client_lock:
        now = monotonic()

        # Poda oportunista de clientes inactivos
        if len(_ai_client_calls) > 200:
            expired_keys = [
                k for k, timestamps in _ai_client_calls.items()
                if not timestamps or now - timestamps[-1] > 60
            ]
            for k in expired_keys:
                _ai_client_calls.pop(k, None)

        if client_identifier not in _ai_client_calls:
            _ai_client_calls[client_identifier] = deque()
        calls = _ai_client_calls[client_identifier]
        while calls and now - calls[0] > 60:
            calls.popleft()
        if len(calls) >= max_per_minute:
            logger.warning("[AIRateLimit] Límite de %d llamadas/min alcanzado para %s.", max_per_minute, client_identifier)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Has alcanzado el límite de {max_per_minute} fotos o consultas por minuto. Estamos en fase de pruebas para proteger los recursos de la API.",
                headers={"X-Error-Code": "rate_limited"},
            )
        calls.append(now)


# ── Topes por usuario en ventana deslizante (en memoria, por proceso) ──
# Cada clave guarda SU propia ventana junto a sus entradas (token, instante): la poda usa la ventana de cada una,
# así una búsqueda de 1 h nunca borra entradas de visión o recetas de 24 h.
_user_quota: dict[tuple, tuple[float, deque]] = {}
_user_quota_seq = 0
_USER_QUOTA_PRUNE_THRESHOLD = 500
NO_QUOTA_TOKEN = 0  # «sin tope por usuario»: no se registró nada, devolverlo no hace nada


def consume_user_quota(bucket: str, user_id: str, limit: int, window_seconds: float) -> Optional[int]:
    """Gasta una unidad del tope de `user_id` en `bucket`. Devuelve un token (para `refund_user_quota`) o
    None si ya se agotó en la ventana.

    Síncrono y sin await entre comprobar y registrar (seguro entre tareas asyncio). `limit <= 0` o sin
    `user_id` = sin tope por usuario (devuelve NO_QUOTA_TOKEN sin registrar nada). Compara con `is None`.
    """
    global _user_quota_seq
    if not user_id or limit <= 0:
        return NO_QUOTA_TOKEN
    now = monotonic()
    if len(_user_quota) > _USER_QUOTA_PRUNE_THRESHOLD:
        for k in [k for k, (win, d) in _user_quota.items() if not d or now - d[-1][1] > win]:
            _user_quota.pop(k, None)
    key = (bucket, user_id)
    entry = _user_quota.get(key)
    if entry is None:
        entry = _user_quota[key] = (window_seconds, deque())
    _, calls = entry
    _user_quota[key] = (window_seconds, calls)
    while calls and now - calls[0][1] > window_seconds:
        calls.popleft()
    if len(calls) >= limit:
        return None
    _user_quota_seq += 1
    calls.append((_user_quota_seq, now))
    return _user_quota_seq


def refund_user_quota(bucket: str, user_id: str, token: Optional[int]) -> None:
    """Devuelve la unidad EXACTA reservada (la del token); si ya salió de la ventana o no hubo tope, no hace nada."""
    if not token:
        return
    entry = _user_quota.get((bucket, user_id))
    if not entry:
        return
    calls = entry[1]
    for item in calls:
        if item[0] == token:
            calls.remove(item)
            return


def check_user_daily_recipes(bucket: str, user_id: str) -> Optional[int]:
    """Tope diario de generaciones por usuario (RECIPES_PER_USER_DAILY); 429 con mensaje claro al superarlo.
    Devuelve el token de la unidad gastada (para devolverla con `refund_user_daily_recipes` si el servidor falla)."""
    from app.core.config import settings  # import local: evita ciclos al cargar el módulo

    token = consume_user_quota(f"recipes_{bucket}", user_id, settings.RECIPES_PER_USER_DAILY, 24 * 60 * 60)
    if token is None:
        logger.warning("[RateLimit] Tope diario de recetas (%s) alcanzado para %s.", bucket, user_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Llegaste al límite diario de recetas. Vuelve mañana.",
            headers={"X-Error-Code": "daily_recipe_limit"},
        )
    return token


def refund_user_daily_recipes(bucket: str, user_id: str, token: Optional[int]) -> None:
    refund_user_quota(f"recipes_{bucket}", user_id, token)


def reset_rate_limits():
    """Limpia todos los estados en memoria de rate limiting (utilizado para tests de integración)."""
    global _total_calls
    _calls.clear()
    _total_calls = 0
    _auth_attempts.clear()
    _ai_client_calls.clear()
    _user_quota.clear()
