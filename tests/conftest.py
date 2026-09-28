import os
import tempfile

# Aislamiento obligatorio ANTES de importar la app: los tests nunca deben tocar Supabase (reset_db
# vacía tablas) ni la base local de desarrollo. Las variables reales tienen prioridad sobre .env.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_KEY"] = ""
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="foodai-tests-"), "test.db")

import pytest
from app.core.db import reset_db, init_db
from app.core.rate_limit import reset_rate_limits
from app.core.security import create_jwt_token


@pytest.fixture(autouse=True)
def clean_db_for_tests():
    """Garantiza que la base de datos y rate limiters comiencen en un estado limpio para cada test."""
    init_db()
    reset_db()
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture
def auth_headers():
    """Cabecera Bearer válida para el usuario demo sembrado en la base SQLite de tests."""
    token = create_jwt_token({"sub": "usr-demo-1", "email": "demo@foodai.com"})
    return {"Authorization": f"Bearer {token}"}
