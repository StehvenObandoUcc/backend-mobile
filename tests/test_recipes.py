from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_get_recipes_returns_list():
    """GET /api/v1/recipes debe responder con HTTP 200 y una lista no vacía de recetas."""
    response = client.get("/api/v1/recipes")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2
    assert "title" in data[0]
    assert "steps" in data[0]


def test_generate_recipes_with_custom_ingredients():
    """POST /api/v1/recipes/generate debe recibir ingredientes y generar recetas con ellos."""
    payload = {
        "ingredients": [
            {"id": "ing-1", "name": "Pechuga de pollo", "category": "protein"},
            {"id": "ing-2", "name": "Zanahoria fresca", "category": "vegetable"},
            {"id": "ing-3", "name": "Cebolla", "category": "vegetable"},
        ],
        "max_prep_time": 20,
        "focus": "waste_reduction",
    }
    response = client.post("/api/v1/recipes/generate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    first_recipe = data[0]
    assert "title" in first_recipe
    assert any(word in first_recipe["title"].lower() for word in ["pollo", "receta", "salteado", "zanahoria", "cebolla"])
    assert first_recipe["prepTimeMinutes"] <= 25
    assert isinstance(first_recipe["steps"], list)


def test_generate_recipes_with_empty_ingredients_fallback():
    """POST /api/v1/recipes/generate con lista vacía debe generar sugerencias balanceadas sin fallar."""
    response = client.post("/api/v1/recipes/generate", json={"ingredients": []})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "title" in data[0]


def test_generate_recipes_with_dietary_preference():
    """POST /api/v1/recipes/generate debe soportar y respetar la preferencia dietaria."""
    payload = {
        "ingredients": [
            {"id": "ing-1", "name": "Tomate", "category": "vegetable"},
            {"id": "ing-2", "name": "Cebolla", "category": "vegetable"},
            {"id": "ing-3", "name": "Espinaca", "category": "vegetable"},
        ],
        "max_prep_time": 20,
        "focus": "healthy",
        "dietary_preference": "vegetarian",
    }
    response = client.post("/api/v1/recipes/generate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "title" in data[0]


def test_generate_recipes_with_custom_focus():
    """POST /api/v1/recipes/generate debe soportar el enfoque culinario personalizado."""
    payload = {
        "ingredients": [
            {"id": "ing-1", "name": "Pasta", "category": "grain"},
            {"id": "ing-2", "name": "Queso", "category": "dairy"},
        ],
        "max_prep_time": 30,
        "focus": "custom: salsa cremosa",
        "difficulty": "medium",
    }
    response = client.post("/api/v1/recipes/generate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "title" in data[0]

