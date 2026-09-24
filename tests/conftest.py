import pytest
from app.core.db import reset_db, init_db
from app.core.rate_limit import reset_rate_limits


@pytest.fixture(autouse=True)
def clean_db_for_tests():
    """Garantiza que la base de datos y rate limiters comiencen en un estado limpio para cada test."""
    init_db()
    reset_db()
    reset_rate_limits()
    yield
    reset_rate_limits()

