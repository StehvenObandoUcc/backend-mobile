import pytest
import uuid
import concurrent.futures
from fastapi.testclient import TestClient
from fastapi import HTTPException
from app.main import app
from app.core import db
from app.core.security import create_jwt_token

client = TestClient(app)

USER_A_ID = "usr-b2-test-a"
USER_A_EMAIL = "b2_a@foodai.com"
USER_B_ID = "usr-b2-test-b"
USER_B_EMAIL = "b2_b@foodai.com"

TOKEN_USER_A = create_jwt_token({"sub": USER_A_ID, "email": USER_A_EMAIL})
TOKEN_USER_B = create_jwt_token({"sub": USER_B_ID, "email": USER_B_EMAIL})

AUTH_HEADER_A = {"Authorization": f"Bearer {TOKEN_USER_A}"}
AUTH_HEADER_B = {"Authorization": f"Bearer {TOKEN_USER_B}"}


@pytest.fixture(autouse=True)
def ensure_users():
    """Garantiza la existencia de usuarios de prueba tanto en SQLite como en Supabase."""
    with db.get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, name, password_hash) VALUES (?, ?, ?, ?)",
            (USER_A_ID, USER_A_EMAIL, "Usuario B2 A", "hashA"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO users (id, email, name, password_hash) VALUES (?, ?, ?, ?)",
            (USER_B_ID, USER_B_EMAIL, "Usuario B2 B", "hashB"),
        )
        conn.commit()

    sb = db.get_supabase()
    if sb:
        try:
            sb.table("users").upsert([
                {"id": USER_A_ID, "email": USER_A_EMAIL, "name": "Usuario B2 A", "password_hash": "hashA"},
                {"id": USER_B_ID, "email": USER_B_EMAIL, "name": "Usuario B2 B", "password_hash": "hashB"},
            ]).execute()
        except Exception:
            pass


# ─── INVENTORY TESTS ─────────────────────────────────────────────────────────

def test_inventory_http_endpoints_status_codes():
    """Valida la matriz completa de códigos HTTP para POST /api/v1/inventory:
    - 201 en creación nueva;
    - 200 en repetición idempotente con payload idéntico;
    - 409 en conflicto de payload (mismo ID, datos distintos);
    - 409 en conflicto de usuario (mismo ID, usuario ajeno);
    - 422 en payload inválido.
    """
    item_id = f"ing-http-{uuid.uuid4().hex[:8]}"
    valid_payload = {
        "id": item_id,
        "name": "Tomates Secos",
        "category": "vegetable",
        "quantity": 300,
        "unit": "grams",
    }

    # 1. Creación nueva -> 201 Created
    res1 = client.post("/api/v1/inventory", json=valid_payload, headers=AUTH_HEADER_A)
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["id"] == item_id
    created_at = data1["createdAt"]

    # 2. Repetición idempotente (retry tras timeout) -> 200 OK
    res2 = client.post("/api/v1/inventory", json=valid_payload, headers=AUTH_HEADER_A)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["id"] == item_id
    assert data2["createdAt"] == created_at

    # 3. Mismo ID con payload diferente (conflicto de payload) -> 409 Conflict
    diff_payload = {
        "id": item_id,
        "name": "Tomates Fritos",
        "category": "vegetable",
    }
    res3 = client.post("/api/v1/inventory", json=diff_payload, headers=AUTH_HEADER_A)
    assert res3.status_code == 409

    # 4. Mismo ID intentado por otro usuario -> 409 Conflict
    res4 = client.post("/api/v1/inventory", json=valid_payload, headers=AUTH_HEADER_B)
    assert res4.status_code == 409

    # 5. Payload inválido -> 422 Unprocessable Entity
    invalid_payload = {
        "name": "",
    }
    res5 = client.post("/api/v1/inventory", json=invalid_payload, headers=AUTH_HEADER_A)
    assert res5.status_code == 422

    # Limpieza
    db.delete_ingredient(item_id, USER_A_ID)


def test_inventory_concurrent_creates_same_id_and_user():
    """Valida que dos peticiones simultáneas con el mismo ID y usuario no fallen por carrera y no dupliquen."""
    item_id = f"ing-race-{uuid.uuid4().hex[:8]}"
    payload = {
        "id": item_id,
        "name": "Avena en Hojuelas",
        "category": "grain",
        "quantity": 1,
        "unit": "kg",
    }

    def post_item():
        c = TestClient(app)
        return c.post("/api/v1/inventory", json=payload, headers=AUTH_HEADER_A)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(post_item)
        f2 = executor.submit(post_item)
        r1 = f1.result()
        r2 = f2.result()

    assert r1.status_code in (200, 201)
    assert r2.status_code in (200, 201)

    list_res = client.get("/api/v1/inventory", headers=AUTH_HEADER_A)
    matches = [x for x in list_res.json() if x["id"] == item_id]
    assert len(matches) == 1

    # Limpieza
    db.delete_ingredient(item_id, USER_A_ID)


# ─── SHOPPING TESTS ──────────────────────────────────────────────────────────

def test_shopping_http_endpoints_status_codes():
    """Valida la matriz completa de códigos HTTP para POST /api/v1/shopping:
    - 201 en creación nueva;
    - 200 en repetición idempotente con payload idéntico;
    - 409 en conflicto de payload;
    - 409 en conflicto de usuario;
    - 422 en payload inválido.
    """
    item_id = f"shop-http-{uuid.uuid4().hex[:8]}"
    valid_payload = {
        "id": item_id,
        "name": "Aceite de Coco",
        "quantity": 500,
        "unit": "ml",
        "category": "oil",
    }

    # 1. Creación nueva -> 201 Created
    res1 = client.post("/api/v1/shopping", json=valid_payload, headers=AUTH_HEADER_A)
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["id"] == item_id
    created_at = data1["createdAt"]

    # 2. Repetición idempotente (retry tras timeout) -> 200 OK
    res2 = client.post("/api/v1/shopping", json=valid_payload, headers=AUTH_HEADER_A)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["id"] == item_id
    assert data2["createdAt"] == created_at

    # 3. Conflicto de payload con datos distintos -> 409 Conflict
    diff_payload = {
        "id": item_id,
        "name": "Aceite de Canola",
        "category": "oil",
    }
    res3 = client.post("/api/v1/shopping", json=diff_payload, headers=AUTH_HEADER_A)
    assert res3.status_code == 409

    # 4. Conflicto de usuario ajeno -> 409 Conflict
    res4 = client.post("/api/v1/shopping", json=valid_payload, headers=AUTH_HEADER_B)
    assert res4.status_code == 409

    # 5. Payload inválido -> 422 Unprocessable Entity
    invalid_payload = {
        "quantity": 10,
    }
    res5 = client.post("/api/v1/shopping", json=invalid_payload, headers=AUTH_HEADER_A)
    assert res5.status_code == 422

    # Limpieza
    db.delete_shopping_item(item_id, USER_A_ID)


def test_shopping_concurrent_creates_same_id_and_user():
    """Valida que dos peticiones concurrentes de shopping con el mismo ID no generen error 500 ni dupliquen."""
    item_id = f"shop-race-{uuid.uuid4().hex[:8]}"
    payload = {
        "id": item_id,
        "name": "Harina de Trigo",
        "quantity": 1,
        "unit": "kg",
        "category": "grain",
    }

    def post_shopping():
        c = TestClient(app)
        return c.post("/api/v1/shopping", json=payload, headers=AUTH_HEADER_A)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(post_shopping)
        f2 = executor.submit(post_shopping)
        r1 = f1.result()
        r2 = f2.result()

    assert r1.status_code in (200, 201)
    assert r2.status_code in (200, 201)

    list_res = client.get("/api/v1/shopping", headers=AUTH_HEADER_A)
    matches = [x for x in list_res.json() if x["id"] == item_id]
    assert len(matches) == 1

    # Limpieza
    db.delete_shopping_item(item_id, USER_A_ID)


def test_db_layer_idempotency_preserves_created_at_and_user():
    """Validación directa a nivel de base de datos de preservación de created_at e integridad."""
    item_id = f"ing-raw-{uuid.uuid4().hex[:8]}"
    original_created_at = "2026-01-01T00:00:00Z"
    item_data = {
        "id": item_id,
        "name": "Romero Seco",
        "category": "vegetable",
        "created_at": original_created_at,
    }

    # 1. Primera inserción
    rec1 = db.create_ingredient(item_data, user_id=USER_A_ID)
    assert rec1["id"] == item_id
    assert rec1["userId"] == USER_A_ID

    # 2. Reintento idéntico conserva created_at y user_id
    rec2 = db.create_ingredient(
        {"id": item_id, "name": "Romero Seco", "category": "vegetable", "created_at": "2026-09-01T00:00:00Z"},
        user_id=USER_A_ID,
    )
    assert rec2["id"] == item_id
    assert rec2["createdAt"] == rec1["createdAt"]
    assert rec2["userId"] == USER_A_ID

    # 3. Payload diferente para el mismo ID produce 409
    with pytest.raises(HTTPException) as exc_diff:
        db.create_ingredient({"id": item_id, "name": "Albahaca", "category": "vegetable"}, user_id=USER_A_ID)
    assert exc_diff.value.status_code == 409

    # 4. Usuario ajeno produce 409
    with pytest.raises(HTTPException) as exc_user:
        db.create_ingredient({"id": item_id, "name": "Romero Seco", "category": "vegetable"}, user_id=USER_B_ID)
    assert exc_user.value.status_code == 409

    # Limpieza
    db.delete_ingredient(item_id, USER_A_ID)
