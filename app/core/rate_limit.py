import asyncio
import logging
from collections import deque
from time import monotonic
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_calls = deque()

# Costo aproximado por llamada a DeepSeek Flash (con imagen y prompt ~600 tokens): ~$0.00015 USD
ESTIMATED_COST_PER_CALL_USD = 0.00015
_total_calls = 0

async def check_rate_limit(max_per_minute: int = 10):
    global _total_calls
    async with _lock:
        now = monotonic()
        while _calls and now - _calls[0] > 60:
            _calls.popleft()
        if len(_calls) >= max_per_minute:
            logger.warning("[RateLimit] Límite de %d llamadas/min alcanzado.", max_per_minute)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiadas solicitudes a la IA en poco tiempo. Espera un momento para proteger tu saldo."
            )
        _calls.append(now)
        _total_calls += 1
        logger.info(
            "[AI Cost Log] Llamada #%d registrada. Costo estimado acumulado: ~$%.5f USD.",
            _total_calls,
            _total_calls * ESTIMATED_COST_PER_CALL_USD,
        )
