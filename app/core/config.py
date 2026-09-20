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

    # AI Config (DeepSeek)
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "deepseek")
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    DEEPSEEK_TIMEOUT_SECONDS: float = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "45"))
    MAX_CALLS_PER_MINUTE: int = int(os.getenv("MAX_CALLS_PER_MINUTE", "10"))


settings = Settings()
