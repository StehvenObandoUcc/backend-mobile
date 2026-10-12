from typing import List
import logging
import os

_config_logger = logging.getLogger(__name__)

def _load_env():
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env_path = os.path.join(backend_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    # Las variables reales del entorno tienen prioridad sobre el archivo .env
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()


def _auth_provider_is_invalid() -> bool:
    value = os.getenv("AUTH_PROVIDER", "local").strip().lower() or "local"
    return value not in ("local", "supabase")


def _resolve_auth_provider() -> str:
    """AUTH_PROVIDER válido: "local" (por defecto) o "supabase". Cualquier otro valor cae en "local"."""
    value = os.getenv("AUTH_PROVIDER", "local").strip().lower() or "local"
    if value not in ("local", "supabase"):
        _config_logger.warning("AUTH_PROVIDER inválido (%r): se usará 'local'.", value)
        return "local"
    return value


def _float_env(name: str, default: float) -> float:
    """Lee un float del entorno; si el valor es inválido usa el predeterminado con un warning."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        _config_logger.warning("%s inválido (%r): se usará %s.", name, raw, default)
        return default


class Settings:
    PROJECT_NAME: str = "Food AI Backend"
    VERSION: str = "0.3.0"
    API_V1_STR: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["*"]

    # Security & Auth
    DEFAULT_INSECURE_SECRET: str = "food-ai-secret-key-change-in-production-123456"
    JWT_SECRET: str = os.getenv("JWT_SECRET", DEFAULT_INSECURE_SECRET)
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRATION_SECONDS: int = int(os.getenv("JWT_EXPIRATION_SECONDS", str(60 * 60 * 24 * 7)))

    def is_production_secure(self) -> bool:
        """Verifica si la configuración de seguridad es apta para producción."""
        return self.JWT_SECRET != self.DEFAULT_INSECURE_SECRET and len(self.JWT_SECRET) >= 32

    # Entorno: Heroku define DYNO en cada contenedor; APP_ENV=production lo fuerza en otros hosts
    APP_ENV: str = os.getenv("APP_ENV", "production" if os.getenv("DYNO") else "development").lower()

    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    # Database
    DATABASE_PATH: str = os.getenv(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "app.db"),
    )
    # Supabase (Si está configurado, la base de datos se conecta a la nube en Supabase)
    _raw_supabase_url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    if _raw_supabase_url.endswith("/rest/v1"):
        _raw_supabase_url = _raw_supabase_url[:-8].rstrip("/")
    SUPABASE_URL: str = _raw_supabase_url
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "").strip()

    # Proveedor de autenticación: "local" (PBKDF2 propio) o "supabase" (Supabase Auth como dueño de
    # la contraseña y del correo verificado; el backend sigue emitiendo su propio JWT)
    AUTH_PROVIDER: str = _resolve_auth_provider()
    AUTH_PROVIDER_INVALID: bool = _auth_provider_is_invalid()  # en producción impide arrancar
    SUPABASE_AUTH_TIMEOUT_SECONDS: float = _float_env("SUPABASE_AUTH_TIMEOUT_SECONDS", 10.0)
    # Opcional: clave pública (anon) para las llamadas de usuario a Supabase Auth; /admin siempre usa SUPABASE_KEY
    SUPABASE_ANON_KEY: str = os.getenv("SUPABASE_ANON_KEY", "").strip()

    # AI Config (DeepSeek)
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "deepseek")
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    DEEPSEEK_CHAT_MODEL: str = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    DEEPSEEK_TIMEOUT_SECONDS: float = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "45"))
    MAX_CALLS_PER_MINUTE: int = int(os.getenv("MAX_CALLS_PER_MINUTE", "5"))

    # Foto real verificada de cada receta (Unsplash modo demo, 50 búsquedas/hora para toda la app).
    # Sin UNSPLASH_ACCESS_KEY no se hace ninguna llamada y las recetas salen sin foto.
    UNSPLASH_ACCESS_KEY: str = os.getenv("UNSPLASH_ACCESS_KEY", "").strip()
    UNSPLASH_APP_NAME: str = os.getenv("UNSPLASH_APP_NAME", "food_ai_assistant").strip() or "food_ai_assistant"
    # Si es true, el endpoint solo entrega recetas que tengan foto verificada
    PHOTO_REQUIRED: bool = os.getenv("PHOTO_REQUIRED", "false").strip().lower() in ("true", "1")
    # Tope diario de llamadas de visión (únicas con costo) y presupuesto de tiempo total de las fotos
    VISION_DAILY_CAP: int = int(os.getenv("VISION_DAILY_CAP", "50"))
    PHOTO_TIME_BUDGET_SECONDS: float = float(os.getenv("PHOTO_TIME_BUDGET_SECONDS", "20"))


settings = Settings()

