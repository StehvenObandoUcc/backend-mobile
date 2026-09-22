from typing import List
import os

def _load_env():
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    env_path = os.path.join(backend_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

_load_env()


class Settings:
    PROJECT_NAME: str = "Food AI Backend"
    VERSION: str = "0.3.0"
    API_V1_STR: str = "/api/v1"
    CORS_ORIGINS: List[str] = ["*"]

    # Security & Auth
    JWT_SECRET: str = os.getenv("JWT_SECRET", "food-ai-secret-key-change-in-production-123456")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_EXPIRATION_SECONDS: int = int(os.getenv("JWT_EXPIRATION_SECONDS", str(60 * 60 * 24 * 7)))

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

    # AI Config (DeepSeek)
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "deepseek")
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    DEEPSEEK_TIMEOUT_SECONDS: float = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "45"))
    MAX_CALLS_PER_MINUTE: int = int(os.getenv("MAX_CALLS_PER_MINUTE", "10"))


settings = Settings()
