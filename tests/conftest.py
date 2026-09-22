import pytest
from app.core.db import reset_db, init_db


@pytest.fixture(autouse=True)
def clean_db_for_tests():
    """Garantiza que la base de datos comience y termine en un estado limpio para cada test."""
    init_db()
    reset_db()
    yield
    reset_db()
