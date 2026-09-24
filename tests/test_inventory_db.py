import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core import db
from app.core.security import create_jwt_token

client = TestClient(app)

USER_1_ID = "usr-chef-1"
USER_1_EMAIL = "chef1@foodai.com"
USER_2_ID = "usr-chef-2"
USER_2_EMAIL = "chef2@foodai.com"

TOKEN_USER_1 = create_jwt_token({"sub": USER_1_ID, "email": USER_1_EMAIL})
TOKEN_USER_2 = create_jwt_token({"sub": USER_2_ID, "email": USER_2_EMAIL})

AUTH_HEADER_1 = {"Authorization": f"Bearer {TOKEN_USER_1}"}
AUTH_HEADER_2 = {"Authorization": f"Bearer {TOKEN_USER_2}"}


@pytest.fixture(autouse=True)
def setup_users():
    """Asegura que los usuarios de prueba existan en la base de datos."""
    if not db.get_user_by_id(USER_1_ID):
        db.create_user(USER_1_ID, USER_1_EMAIL, "Chef Uno", "hash1")
    if not db.get_user_by_id(USER_2_ID):
        db.create_user(USER_2_ID, USER_2_EMAIL, "Chef Dos", "hash2")


def test_create_and_preserve_all_fields_in_db():
    """Auditoría: Verifica que un alimento se inserte y que el 100% de los campos se conserven en la BD."""
    payload = {
        "name": "Tomates Perita Orgánicos",
        "category": "vegetable",
        "quantity": 4.5,
        "unit": "kilograms",
        "expirationDate": "2026-10-15",
        "confidence": 0.96,
        "source": "ai",
        "confirmed": True,
        "imageUri": "file:///storage/emulated/0/DCIM/tomates.jpg",
        "notes": "Comprados en la feria ecológica local, muy frescos",
        "expirationSource": "label",
    }

    response = client.post("/api/v1/inventory", json=payload, headers=AUTH_HEADER_1)
    assert response.status_code == 201
    created_data = response.json()

    item_id = created_data["id"]
    assert item_id.startswith("ing-")

    # 1. Comprobar que la respuesta contiene todos los campos intactos
    assert created_data["name"] == payload["name"]
    assert created_data["category"] == payload["category"]
    assert created_data["quantity"] == payload["quantity"]
    assert created_data["unit"] == payload["unit"]
    assert created_data["expirationDate"] == payload["expirationDate"]
    assert created_data["confidence"] == payload["confidence"]
    assert created_data["source"] == payload["source"]
    assert created_data["confirmed"] is True
    assert created_data["imageUri"] == payload["imageUri"]
    assert created_data["notes"] == payload["notes"]
    assert created_data["expirationSource"] == payload["expirationSource"]
    assert "createdAt" in created_data
    assert "updatedAt" in created_data

    # 2. Comprobar directamente en la base de datos (Query física SQL)
    db_record = db.get_ingredient_by_id(item_id, USER_1_ID)
    assert db_record is not None
    assert db_record["id"] == item_id
    assert db_record["user_id"] == USER_1_ID
    assert db_record["name"] == "Tomates Perita Orgánicos"
    assert db_record["category"] == "vegetable"
    assert float(db_record["quantity"]) == 4.5
    assert db_record["unit"] == "kilograms"
    assert db_record["expiration_date"] == "2026-10-15"
    assert float(db_record["confidence"]) == 0.96
    assert db_record["source"] == "ai"
    assert bool(db_record["confirmed"]) is True
    assert db_record["image_uri"] == "file:///storage/emulated/0/DCIM/tomates.jpg"
    assert db_record["notes"] == "Comprados en la feria ecológica local, muy frescos"
    assert db_record["expiration_source"] == "label"


def test_update_ingredient_effectively_in_db():
    """Auditoría: Verifica que al actualizar un alimento, los cambios se guarden en la BD y se preserve el resto."""
    # 1. Crear alimento inicial
    initial = {
        "name": "Leche Entera",
        "category": "dairy",
        "quantity": 2,
        "unit": "liters",
        "expirationDate": "2026-09-30",
        "confidence": 0.90,
        "source": "manual",
        "confirmed": True,
        "notes": "Tetrapak cerrado",
    }
    create_res = client.post("/api/v1/inventory", json=initial, headers=AUTH_HEADER_1)
    assert create_res.status_code == 201
    item_id = create_res.json()["id"]

    # 2. Actualizar solo la cantidad, fecha de vencimiento y notas
    updates = {
        "quantity": 1,
        "expirationDate": "2026-10-05",
        "notes": "Abierto ayer, refrigerar bien",
    }
    update_res = client.put(f"/api/v1/inventory/{item_id}", json=updates, headers=AUTH_HEADER_1)
    assert update_res.status_code == 200
    updated_data = update_res.json()

    assert updated_data["quantity"] == 1
    assert updated_data["expirationDate"] == "2026-10-05"
    assert updated_data["notes"] == "Abierto ayer, refrigerar bien"
    # Campos no modificados deben conservarse intactos
    assert updated_data["name"] == "Leche Entera"
    assert updated_data["category"] == "dairy"
    assert updated_data["unit"] == "liters"
    assert updated_data["confidence"] == 0.90

    # 3. Verificar directamente en la base de datos
    db_item = db.get_ingredient_by_id(item_id, USER_1_ID)
    assert db_item is not None
    assert float(db_item["quantity"]) == 1.0
    assert db_item["expiration_date"] == "2026-10-05"
    assert db_item["notes"] == "Abierto ayer, refrigerar bien"
    assert db_item["name"] == "Leche Entera"
    assert db_item["unit"] == "liters"


def test_delete_ingredient_effectively_in_db():
    """Auditoría: Verifica que al borrar un alimento, este desaparezca físicamente de la base de datos."""
    # 1. Crear alimento
    create_res = client.post(
        "/api/v1/inventory",
        json={"name": "Yogurt de Frutilla", "category": "dairy", "quantity": 1, "unit": "units"},
        headers=AUTH_HEADER_1,
    )
    assert create_res.status_code == 201
    item_id = create_res.json()["id"]

    # Confirmar que existe en la BD
    assert db.get_ingredient_by_id(item_id, USER_1_ID) is not None

    # 2. Borrar alimento vía DELETE endpoint
    delete_res = client.delete(f"/api/v1/inventory/{item_id}", headers=AUTH_HEADER_1)
    assert delete_res.status_code == 200
    assert delete_res.json()["status"] == "deleted"

    # 3. Verificar que YA NO existe en la base de datos
    assert db.get_ingredient_by_id(item_id, USER_1_ID) is None

    # 4. Intentar borrarlo de nuevo debe devolver 404
    delete_again = client.delete(f"/api/v1/inventory/{item_id}", headers=AUTH_HEADER_1)
    assert delete_again.status_code == 404

    # 5. Intentar actualizar el ítem borrado debe devolver 404
    update_deleted = client.put(
        f"/api/v1/inventory/{item_id}",
        json={"quantity": 5},
        headers=AUTH_HEADER_1,
    )
    assert update_deleted.status_code == 404


def test_batch_delete_ingredients_in_db():
    """Auditoría: Verifica el borrado masivo de alimentos en lote en la base de datos."""
    res1 = client.post("/api/v1/inventory", json={"name": "Manzana 1", "category": "fruit"}, headers=AUTH_HEADER_1)
    res2 = client.post("/api/v1/inventory", json={"name": "Manzana 2", "category": "fruit"}, headers=AUTH_HEADER_1)
    res3 = client.post("/api/v1/inventory", json={"name": "Pera Conservada", "category": "fruit"}, headers=AUTH_HEADER_1)

    id1 = res1.json()["id"]
    id2 = res2.json()["id"]
    id3 = res3.json()["id"]

    # Borrar id1 e id2 en lote
    batch_res = client.post(
        "/api/v1/inventory/batch-delete",
        json={"ids": [id1, id2]},
        headers=AUTH_HEADER_1,
    )
    assert batch_res.status_code == 200
    assert batch_res.json()["count"] == 2

    # Verificar que id1 e id2 ya no existen en la BD
    assert db.get_ingredient_by_id(id1, USER_1_ID) is None
    assert db.get_ingredient_by_id(id2, USER_1_ID) is None

    # Verificar que id3 se mantuvo intacto en la BD
    item3 = db.get_ingredient_by_id(id3, USER_1_ID)
    assert item3 is not None
    assert item3["name"] == "Pera Conservada"


def test_multi_user_isolation_for_delete_and_update():
    """Auditoría de Seguridad: Un usuario no puede ver, modificar ni borrar alimentos de otro usuario."""
    # Crear alimento con Usuario 1
    res1 = client.post(
        "/api/v1/inventory",
        json={"name": "Queso Parmesano Privado", "category": "dairy"},
        headers=AUTH_HEADER_1,
    )
    item_id = res1.json()["id"]

    # Usuario 2 intenta modificar el alimento de Usuario 1 -> Debe fallar con 404
    hack_update = client.put(
        f"/api/v1/inventory/{item_id}",
        json={"quantity": 99},
        headers=AUTH_HEADER_2,
    )
    assert hack_update.status_code == 404

    # Usuario 2 intenta borrar el alimento de Usuario 1 -> Debe fallar con 404
    hack_delete = client.delete(
        f"/api/v1/inventory/{item_id}",
        headers=AUTH_HEADER_2,
    )
    assert hack_delete.status_code == 404

    # Verificar que el alimento sigue intacto en la BD para el Usuario 1
    persisted = db.get_ingredient_by_id(item_id, USER_1_ID)
    assert persisted is not None
    assert persisted["name"] == "Queso Parmesano Privado"


def test_input_validation_and_database_rejection():
    """Auditoría: Validaciones estrictas previenen corrupción de datos en la base de datos."""
    # 1. Cantidad negativa rechazada
    bad_qty = client.post(
        "/api/v1/inventory",
        json={"name": "Pollo", "quantity": -5},
        headers=AUTH_HEADER_1,
    )
    assert bad_qty.status_code == 422

    # 2. Nombre vacío rechazado
    empty_name = client.post(
        "/api/v1/inventory",
        json={"name": "   ", "quantity": 1},
        headers=AUTH_HEADER_1,
    )
    assert empty_name.status_code == 422

    # 3. Fecha con formato inválido rechazada
    bad_date = client.post(
        "/api/v1/inventory",
        json={"name": "Yogurt", "expirationDate": "31-12-2026"},
        headers=AUTH_HEADER_1,
    )
    assert bad_date.status_code == 422


def test_inventory_requires_valid_bearer_token():
    """Auditoría de Seguridad (S1): Peticiones anónimas o con tokens corruptos deben devolver 401 Unauthorized."""
    # 1. Petición sin header Authorization
    no_auth = client.get("/api/v1/inventory")
    assert no_auth.status_code == 401
    assert "Se requiere autenticación" in no_auth.json()["detail"]

    # 2. Petición con token falso/corrupto
    bad_token = client.get("/api/v1/inventory", headers={"Authorization": "Bearer token-falso-invalido"})
    assert bad_token.status_code == 401

    # 3. Intento de creación sin autenticación
    no_auth_create = client.post("/api/v1/inventory", json={"name": "Alimento Anónimo"})
    assert no_auth_create.status_code == 401

