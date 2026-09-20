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
