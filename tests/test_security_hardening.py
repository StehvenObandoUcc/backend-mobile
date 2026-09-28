import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import app
from app.core import db
from app.core.config import settings
from app.core.rate_limit import get_client_ip

client = TestClient(app)


def _register(email: str) -> dict:
    res = client.post("/api/v1/auth/register", json={"email": email, "password": "secret123", "name": "Tester"})
    assert res.status_code == 201
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _recipe(recipe_id: str, title: str) -> dict:
    return {
        "id": recipe_id,
        "title": title,
        "description": "d",
        "prepTimeMinutes": 10,
        "servings": 2,
        "difficulty": "easy",
        "matchScore": 90,
        "isSaved": True,
    }


def test_save_recipe_cannot_take_over_another_users_recipe():
    """Un usuario no puede sobrescribir ni apropiarse de una receta ajena reutilizando su ID."""
    victim = _register("victim@test.com")
    attacker = _register("attacker@test.com")

    assert client.post("/api/v1/recipes/save", json=_recipe("rec-shared", "Original"), headers=victim).status_code == 201

    res = client.post("/api/v1/recipes/save", json=_recipe("rec-shared", "Robada"), headers=attacker)
    assert res.status_code == 409

    victim_recipes = client.get("/api/v1/recipes/saved", headers=victim).json()
    assert [r["title"] for r in victim_recipes] == ["Original"]
    assert client.get("/api/v1/recipes/saved", headers=attacker).json() == []


def test_save_recipe_updates_own_recipe():
    headers = _register("owner@test.com")
    client.post("/api/v1/recipes/save", json=_recipe("rec-own", "v1"), headers=headers)
    res = client.post("/api/v1/recipes/save", json=_recipe("rec-own", "v2"), headers=headers)
    assert res.status_code == 201
    titles = [r["title"] for r in client.get("/api/v1/recipes/saved", headers=headers).json()]
    assert titles == ["v2"]


@pytest.mark.parametrize(
    "path,body",
    [
        ("/api/v1/scan", {"image_base64": "aGVsbG8="}),
        ("/api/v1/recipes/generate", {"ingredients": []}),
        ("/api/v1/recipes/steps", {"title": "Tortilla"}),
    ],
)
def test_ai_endpoints_require_authentication(path, body):
    assert client.post(path, json=body).status_code == 401


def _request_with_headers(headers: dict) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": ("10.0.0.1", 1234)})


def test_client_ip_ignores_spoofed_forwarded_for_prefix():
    """El router de Heroku añade la IP real al final; los valores previos los controla el cliente."""
    req = _request_with_headers({"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
    assert get_client_ip(req) == "203.0.113.9"


def test_reset_db_refuses_when_supabase_configured(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_KEY", "service-role-key")
    with pytest.raises(RuntimeError):
        db.reset_db()


def test_shopping_create_normalizes_unit_and_rejects_negative_quantity():
    headers = _register("shopper@test.com")

    res = client.post(
        "/api/v1/shopping",
        json={"id": "shop-long-unit", "name": "Arroz", "unit": "x" * 500, "category": "??"},
        headers=headers,
    )
    assert res.status_code == 201
    assert res.json()["unit"] == "units"
    assert res.json()["category"] == "other"
    # La lista completa debe seguir siendo serializable (antes un unit largo rompía el GET con 500)
    assert client.get("/api/v1/shopping", headers=headers).status_code == 200

    neg = client.post("/api/v1/shopping", json={"name": "Leche", "quantity": -3}, headers=headers)
    assert neg.status_code == 422


class _BrokenSupabase:
    def table(self, _name):
        raise ConnectionError("supabase caído")


def test_supabase_failure_returns_503_instead_of_silent_sqlite_fallback(monkeypatch):
    """Con Supabase configurado, un fallo no debe escribir en SQLite (efímero en Heroku) ni aparentar éxito."""
    from app.api.v1.shopping import get_current_user_id

    # Aislar la escritura: la autenticación no debe ser la que provoque el 503
    app.dependency_overrides[get_current_user_id] = lambda: "usr-demo-1"
    monkeypatch.setattr(db, "get_supabase", lambda: _BrokenSupabase())
    try:
        res = client.post("/api/v1/shopping", json={"id": "shop-x", "name": "Pan"}, headers={})
    finally:
        app.dependency_overrides.clear()
    assert res.status_code == 503

    with db.get_connection() as conn:
        row = conn.execute("SELECT id FROM shopping_items WHERE id = ?", ("shop-x",)).fetchone()
    assert row is None, "El artículo no debe haberse escrito en SQLite como fallback"


def test_production_refuses_insecure_jwt_secret(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "JWT_SECRET", settings.DEFAULT_INSECURE_SECRET)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
