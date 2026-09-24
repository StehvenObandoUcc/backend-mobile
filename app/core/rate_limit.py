import asyncio
import logging
from collections import deque
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
    """Extrae la IP real del cliente considerando encabezados de proxy inverso."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
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
            )
        calls.append(now)


def reset_rate_limits():
    """Limpia todos los estados en memoria de rate limiting (utilizado para tests de integración)."""
    global _total_calls
    _calls.clear()
    _total_calls = 0
    _auth_attempts.clear()
    _ai_client_calls.clear()
