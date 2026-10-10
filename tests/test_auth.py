from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_login_demo_user():
    """POST /api/v1/auth/login debe autenticar al usuario demo y retornar token JWT con expires_at."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "demo@foodai.com", "password": "123456"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "expires_at" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "demo@foodai.com"


def test_login_invalid_password():
    """POST /api/v1/auth/login debe retornar 401 con credenciales inválidas."""
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "demo@foodai.com", "password": "clave_errada"},
    )
    assert response.status_code == 401
    assert "Credenciales incorrectas" in response.json()["detail"]


def test_register_and_get_profile():
    """POST /api/v1/auth/register debe registrar usuario y GET /me debe devolver su perfil."""
    email = "nuevo_chef@foodai.com"
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "clave_segura_123",
            "name": "Chef Carlos",
        },
    )
    assert response.status_code == 201
    data = response.json()
    token = data["access_token"]
    assert "expires_at" in data
    assert data["user"]["email"] == email

    # Verificar /me con el token
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["email"] == email
    assert me_data["name"] == "Chef Carlos"


def test_register_duplicate_email():
    """POST /api/v1/auth/register debe retornar 400 si el email ya existe."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "demo@foodai.com",
            "password": "otra_clave",
            "name": "Duplicado",
        },
    )
    assert response.status_code == 400
    assert "ya se encuentra registrado" in response.json()["detail"]


def test_get_me_without_token():
    """GET /api/v1/auth/me sin token debe devolver 401."""
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_get_me_with_invalid_token():
    """GET /api/v1/auth/me con token inválido o manipulado debe devolver 401."""
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer token.falso.invalido"},
    )
    assert response.status_code == 401
    assert "Token inválido o expirado" in response.json()["detail"]


def _register(email: str, password: str = "secreto123"):
    res = client.post("/api/v1/auth/register", json={"email": email, "password": password, "name": "Prueba"})
    assert res.status_code == 201
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_delete_account_wrong_password_keeps_account():
    """POST /auth/delete-account con contraseña errada responde 401 y no borra nada."""
    headers = _register("borrar1@foodai.com")
    res = client.post("/api/v1/auth/delete-account", json={"password": "otraclave"}, headers=headers)
    assert res.status_code == 401
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200


def test_delete_account_removes_user_and_data():
    """Con la contraseña correcta borra la cuenta y sus datos: el login posterior falla."""
    headers = _register("borrar2@foodai.com")
    client.post("/api/v1/inventory", json={"name": "Tomate", "quantity": 2, "unit": "unit", "category": "vegetable"}, headers=headers)
    res = client.post("/api/v1/auth/delete-account", json={"password": "secreto123"}, headers=headers)
    assert res.status_code == 204
    login = client.post("/api/v1/auth/login", json={"email": "borrar2@foodai.com", "password": "secreto123"})
    assert login.status_code == 401
    from app.core.db import get_connection
    with get_connection() as conn:
        left = conn.execute("SELECT COUNT(*) FROM ingredients WHERE user_id NOT IN (SELECT id FROM users)").fetchone()[0]
    assert left == 0


def test_delete_account_requires_token():
    res = client.post("/api/v1/auth/delete-account", json={"password": "x"})
    assert res.status_code == 401
