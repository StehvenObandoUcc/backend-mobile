import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.db import init_db
from app.api.v1 import api_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Inicialización controlada de base de datos en el ciclo de vida de la aplicación
    init_db()

    # Verificaciones diagnósticas de preparación para despliegue
    if not settings.is_production_secure():
        logger.warning(
            "[DESPLIEGUE] JWT_SECRET está utilizando el valor inseguro por defecto. "
            "Para producción pública, defina un JWT_SECRET robusto en las variables de entorno."
        )
    if not settings.DEEPSEEK_API_KEY:
        logger.warning(
            "[DESPLIEGUE] DEEPSEEK_API_KEY no está configurada. "
            "Las llamadas a escaneo y generación de recetas con IA responderán 503."
        )
    logger.info("[DESPLIEGUE] Límite de IA activo en fase de pruebas: %d llamadas/minuto.", settings.MAX_CALLS_PER_MINUTE)

    # Cliente HTTP persistente con pool de conexiones y keep-alive para llamadas a DeepSeek
    # El timeout se configura por cada llamada individual usando settings.DEEPSEEK_TIMEOUT_SECONDS
    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        yield



app = FastAPI(
    title=settings.PROJECT_NAME,
    description="API para análisis de alimentos y sugerencias inteligentes de recetas",
    version=settings.VERSION,
    lifespan=lifespan,
)

cors_kwargs = {
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if "*" in settings.CORS_ORIGINS:
    cors_kwargs["allow_origins"] = ["*"]
    cors_kwargs["allow_credentials"] = False
else:
    cors_kwargs["allow_origins"] = settings.CORS_ORIGINS
    cors_kwargs["allow_credentials"] = True

app.add_middleware(CORSMiddleware, **cors_kwargs)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.include_router(api_router, prefix=settings.API_V1_STR)

