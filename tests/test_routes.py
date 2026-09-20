import io
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check_returns_200():
    """GET /api/v1/health debe responder con código HTTP 200."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_health_check_returns_status_ok():
    """GET /api/v1/health debe devolver status: ok y service: food-ai-backend."""
    response = client.get("/api/v1/health")
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "food-ai-backend"


def test_scan_accepts_jpeg_and_returns_ingredients():
    """POST /api/v1/scan debe aceptar una imagen JPEG y devolver la propiedad ingredients."""
    fake_jpeg_content = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    files = {
        "image": ("test.jpg", io.BytesIO(fake_jpeg_content), "image/jpeg")
    }

    response = client.post("/api/v1/scan", files=files)
    assert response.status_code == 200

    data = response.json()
    assert "scan_id" in data
    assert "ingredients" in data
    assert isinstance(data["ingredients"], list)
    assert len(data["ingredients"]) >= 1


def test_scan_accepts_base64_json():
    """POST /api/v1/scan debe aceptar JSON con image_base64."""
    payload = {
        "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "mime_type": "image/png"
    }
    response = client.post("/api/v1/scan", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "scan_id" in data
    assert len(data["ingredients"]) >= 1


def test_scan_rejects_empty_payload():
    """POST /api/v1/scan debe responder con HTTP 400 si no se envía imagen ni base64."""
    response = client.post("/api/v1/scan", json={})
    assert response.status_code == 400
