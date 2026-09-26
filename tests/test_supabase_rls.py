"""
Prueba automatizada permanente para verificar Row Level Security (RLS) en Supabase Cloud.

Valida que:
1. Las tablas 'recipes', 'shopping_items', 'ingredients' y 'users' bloqueen el acceso anónimo (SELECT = 0 filas, INSERT = error 42501).
2. El rol de servicio del Backend ('service_role') pueda realizar operaciones legítimas.
3. Los registros insertados con service_role permanezcan invisibles para clientes con anon key.
"""

import os
import pytest
from app.core.config import settings

import json
import base64

ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", settings.SUPABASE_URL or "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", settings.SUPABASE_KEY or "")

def is_service_role_key(key: str) -> bool:
    try:
        parts = key.split(".")
        if len(parts) < 2:
            return False
        padding = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
        return payload.get("role") == "service_role"
    except Exception:
        return False

has_supabase_credentials = bool(
    SUPABASE_URL
    and SUPABASE_KEY
    and is_service_role_key(SUPABASE_KEY)
    and ANON_KEY
)

@pytest.fixture(scope="module")
def clients():
    from supabase import create_client
    anon_client = create_client(SUPABASE_URL, ANON_KEY)
    service_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    return {
        "anon": anon_client,
        "service": service_client
    }

@pytest.mark.skipif(
    not has_supabase_credentials,
    reason="Requiere SUPABASE_URL, SUPABASE_KEY (service_role) y SUPABASE_ANON_KEY configurados en variables de entorno"
)
class TestSupabaseRLSSecurity:

    @pytest.mark.parametrize("table_name", ["recipes", "shopping_items", "ingredients", "users"])
    def test_anon_cannot_read_private_tables(self, clients, table_name):
        """Verifica que un cliente no autenticado (anon key) no pueda leer datos privados."""
        anon = clients["anon"]
        try:
            res = anon.table(table_name).select("*").limit(10).execute()
            assert len(res.data) == 0, f"Vulnerabilidad RLS en {table_name}: anon leyó {len(res.data)} filas"
        except Exception as exc:
            # Si lanza excepción de permiso denegado, RLS está funcionando correctamente
            assert "permission denied" in str(exc).lower() or "42501" in str(exc) or "violates" in str(exc)

    @pytest.mark.parametrize("table_name,payload", [
        ("shopping_items", {"id": "test-anon-rls-shop", "user_id": "usr-fake-1", "name": "Bloqueado"}),
        ("ingredients", {"id": "test-anon-rls-ing", "user_id": "usr-fake-1", "name": "Bloqueado", "category": "fruit", "source": "manual", "confidence": 1.0, "confirmed": True}),
        ("recipes", {"id": "test-anon-rls-rec", "user_id": "usr-fake-1", "title": "Bloqueado", "description": "desc"}),
        ("users", {"id": "usr-anon-rls-fail", "email": "hacker@test.com", "name": "Hacker", "password_hash": "x"}),
    ])
    def test_anon_cannot_insert_into_tables(self, clients, table_name, payload):
        """Verifica que un cliente no autenticado (anon key) no pueda insertar filas arbitrarias."""
        anon = clients["anon"]
        with pytest.raises(Exception) as exc_info:
            anon.table(table_name).insert(payload).execute()
        error_msg = str(exc_info.value).lower()
        assert "42501" in error_msg or "permission denied" in error_msg or "row-level security" in error_msg

    def test_service_role_legitimate_crud_and_anon_isolation(self, clients):
        """Verifica que el backend con service_role opere normalmente y que lo insertado sea invisible para anon."""
        service = clients["service"]
        anon = clients["anon"]
        suffix = os.urandom(4).hex()
        test_user_id = f"usr-rls-regress-{suffix}"
        test_item_id = f"shop-rls-regress-{suffix}"

        # 1. Crear usuario legítimo con service_role
        service.table("users").insert({
            "id": test_user_id,
            "email": f"rls_test_{suffix}@foodai.test",
            "name": "RLS Regress User",
            "password_hash": "dummy_hash"
        }).execute()

        try:
            # 2. Inserción con service_role
            res_insert = service.table("shopping_items").insert({
                "id": test_item_id,
                "user_id": test_user_id,
                "name": "Articulo Regresion RLS"
            }).execute()
            assert len(res_insert.data) == 1
            assert res_insert.data[0]["id"] == test_item_id

            # 3. Lectura con service_role
            res_read = service.table("shopping_items").select("*").eq("id", test_item_id).execute()
            assert len(res_read.data) == 1
            assert res_read.data[0]["name"] == "Articulo Regresion RLS"

            # 4. Anon no debe ver el registro de shopping_items ni el usuario
            res_anon_item = anon.table("shopping_items").select("*").eq("id", test_item_id).execute()
            assert len(res_anon_item.data) == 0, "Anon key fue capaz de leer el articulo creado por service_role!"

            res_anon_user = anon.table("users").select("*").eq("id", test_user_id).execute()
            assert len(res_anon_user.data) == 0, "Anon key fue capaz de leer el usuario creado por service_role!"

        finally:
            # 5. Limpieza garantizada
            try:
                service.table("shopping_items").delete().eq("id", test_item_id).execute()
                service.table("users").delete().eq("id", test_user_id).execute()
            except Exception:
                pass
