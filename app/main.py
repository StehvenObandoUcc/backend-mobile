from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1 import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    "allow_credentials": True,
}
if "*" in settings.CORS_ORIGINS:
    cors_kwargs["allow_origin_regex"] = r"^https?:\/\/.*$"
else:
    cors_kwargs["allow_origins"] = settings.CORS_ORIGINS

app.add_middleware(CORSMiddleware, **cors_kwargs)

app.include_router(api_router, prefix=settings.API_V1_STR)
