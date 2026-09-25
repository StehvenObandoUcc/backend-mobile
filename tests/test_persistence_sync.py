import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core import db
from app.core.security import create_jwt_token

client = TestClient(app)

USER_1_ID = "usr-test-recipe-1"
USER_1_EMAIL = "recipetester1@foodai.com"
USER_2_ID = "usr-test-recipe-2"
USER_2_EMAIL = "recipetester2@foodai.com"

TOKEN_1 = create_jwt_token({"sub": USER_1_ID, "email": USER_1_EMAIL})
TOKEN_2 = create_jwt_token({"sub": USER_2_ID, "email": USER_2_EMAIL})

AUTH_1 = {"Authorization": f"Bearer {TOKEN_1}"}
AUTH_2 = {"Authorization": f"Bearer {TOKEN_2}"}


@pytest.fixture(autouse=True)
def setup_users():
    """Asegura que los usuarios de prueba existan."""
    if not db.get_user_by_id(USER_1_ID):
        db.create_user(USER_1_ID, USER_1_EMAIL, "Chef Recetas 1", "hash1")
    if not db.get_user_by_id(USER_2_ID):
        db.create_user(USER_2_ID, USER_2_EMAIL, "Chef Recetas 2", "hash2")


def test_saved_recipes_crud_and_isolation():
    """Bug 4: Verifica que las recetas se persistan, consulten y eliminen en la base de datos con aislamiento de usuario."""
    recipe_payload = {
        "id": "rec-persisted-1",
        "title": "Pollo al Ajillo con Papas Doradas",
        "description": "Receta clásica española con ajo y perejil.",
        "prepTimeMinutes": 35,
        "servings": 3,
        "difficulty": "medium",
        "matchScore": 92,
        "availableIngredients": [
            {"id": "ing-1", "name": "Pechuga de pollo", "quantity": 500, "unit": "grams", "isAvailable": True, "isOptional": False, "substitutions": []},
            {"id": "ing-2", "name": "Dientes de ajo", "quantity": 6, "unit": "units", "isAvailable": True, "isOptional": False, "substitutions": []},
        ],
        "missingIngredients": [
            {"id": "ms-1", "name": "Vino blanco", "quantity": 100, "unit": "milliliters", "isAvailable": False, "isOptional": True, "substitutions": []},
        ],
        "steps": [
            "Dorar los ajos en láminas.",
            "Agregar el pollo troceado hasta sellar.",
            "Desglasar con vino blanco y cocinar 15 minutos.",
        ],
        "isSaved": True,
        "isPrepared": False,
    }

    # 1. Guardar receta para Usuario 1
    post_res = client.post("/api/v1/recipes/save", json=recipe_payload, headers=AUTH_1)
    assert post_res.status_code == 201
    created_rec = post_res.json()
    assert created_rec["id"] == "rec-persisted-1"
    assert created_rec["title"] == "Pollo al Ajillo con Papas Doradas"
    assert len(created_rec["availableIngredients"]) == 2
    assert len(created_rec["steps"]) == 3

    # 2. Listar recetas de Usuario 1
    list_res_1 = client.get("/api/v1/recipes/saved", headers=AUTH_1)
    assert list_res_1.status_code == 200
    recipes_1 = list_res_1.json()
    assert any(r["id"] == "rec-persisted-1" for r in recipes_1)

    # 3. Aislamiento: Usuario 2 no debe ver las recetas de Usuario 1
    list_res_2 = client.get("/api/v1/recipes/saved", headers=AUTH_2)
    assert list_res_2.status_code == 200
    recipes_2 = list_res_2.json()
    assert not any(r["id"] == "rec-persisted-1" for r in recipes_2)

    # 4. Eliminar receta
    del_res = client.delete("/api/v1/recipes/saved/rec-persisted-1", headers=AUTH_1)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"

    # 5. Confirmar eliminación
    list_after = client.get("/api/v1/recipes/saved", headers=AUTH_1).json()
    assert not any(r["id"] == "rec-persisted-1" for r in list_after)


def test_shopping_list_crud_and_batch():
    """Bug 5: Verifica persistencia, edición y borrado de lista de compras en la base de datos."""
    # 1. Crear ítem
    item_payload = {
        "name": "Aceite de Oliva Extra Virgen",
        "quantity": 500,
        "unit": "milliliters",
        "category": "sauce",
        "isBought": False,
        "recipeSource": "Pollo al Ajillo",
    }
    create_res = client.post("/api/v1/shopping", json=item_payload, headers=AUTH_1)
    assert create_res.status_code == 201
    created_item = create_res.json()
    item_id = created_item["id"]
    assert created_item["name"] == "Aceite de Oliva Extra Virgen"
    assert created_item["isBought"] is False

    # 2. Listar lista de compras
    list_res = client.get("/api/v1/shopping", headers=AUTH_1)
    assert list_res.status_code == 200
    items = list_res.json()
    assert any(it["id"] == item_id for it in items)

    # 3. Marcar como comprado vía PUT
    update_res = client.put(f"/api/v1/shopping/{item_id}", json={"isBought": True}, headers=AUTH_1)
    assert update_res.status_code == 200
    assert update_res.json()["isBought"] is True

    # 4. Batch add desde receta
    batch_payload = {
        "items": [
            {"name": "Pimienta Negra", "quantity": 50, "unit": "grams", "category": "other"},
            {"name": "Romero fresco", "quantity": 1, "unit": "package", "category": "vegetable"},
        ]
    }
    batch_res = client.post("/api/v1/shopping/batch", json=batch_payload, headers=AUTH_1)
    assert batch_res.status_code == 201
    assert len(batch_res.json()) == 2

    # 5. Eliminar artículos comprados
    del_bought_res = client.delete("/api/v1/shopping/bought", headers=AUTH_1)
    assert del_bought_res.status_code == 200
    assert del_bought_res.json()["deleted_count"] >= 1

    # 6. Comprobar que el ítem comprado ya no existe pero los pendientes sí
    remaining = client.get("/api/v1/shopping", headers=AUTH_1).json()
    assert not any(it["id"] == item_id for it in remaining)
    assert any(it["name"] == "Pimienta Negra" for it in remaining)
