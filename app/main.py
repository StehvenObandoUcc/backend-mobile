import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.db import init_db
from app.api.v1 import api_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # En producción un JWT_SECRET inseguro permitiría falsificar tokens de cualquier usuario
    if settings.is_production() and not settings.is_production_secure():
        raise RuntimeError(
            "JWT_SECRET inseguro en producción: defina un secreto aleatorio de al menos 32 caracteres."
        )

    if settings.AUTH_PROVIDER_INVALID and settings.is_production():
        raise RuntimeError("AUTH_PROVIDER inválido en producción: use 'local' o 'supabase'.")

    # AUTH_PROVIDER=supabase necesita URL y clave de Supabase: sin ellas nadie podría iniciar sesión
    if settings.AUTH_PROVIDER == "supabase" and not (settings.SUPABASE_URL and settings.SUPABASE_KEY):
        if settings.is_production():
            raise RuntimeError(
                "AUTH_PROVIDER=supabase requiere SUPABASE_URL y SUPABASE_KEY en producción."
            )
        logger.warning(
            "[DESPLIEGUE] AUTH_PROVIDER=supabase pero faltan SUPABASE_URL o SUPABASE_KEY: "
            "los flujos de autenticación responderán 503."
        )

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
    "expose_headers": ["X-Error-Code"],
}
if "*" in settings.CORS_ORIGINS:
    cors_kwargs["allow_origins"] = ["*"]
    cors_kwargs["allow_credentials"] = False
else:
    cors_kwargs["allow_origins"] = settings.CORS_ORIGINS
    cors_kwargs["allow_credentials"] = True

app.add_middleware(CORSMiddleware, **cors_kwargs)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    """Mismo formato 422 de FastAPI pero sin el campo `input`, para no devolver contraseñas ni códigos."""
    errors = [{key: value for key, value in err.items() if key != "input"} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.include_router(api_router, prefix=settings.API_V1_STR)

