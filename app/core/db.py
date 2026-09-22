import os
import sqlite3
import logging
import datetime
import uuid
from typing import Optional, Dict, Any, List
from app.core.config import settings

logger = logging.getLogger(__name__)

# Cliente de Supabase si las credenciales están configuradas
_supabase_client = None


def get_supabase():
    """Devuelve el cliente de Supabase si SUPABASE_URL y SUPABASE_KEY están presentes."""
    global _supabase_client
    if _supabase_client is None and settings.SUPABASE_URL and settings.SUPABASE_KEY:
        try:
            from supabase import create_client
            _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            logger.info("[DB] Conectado exitosamente a Supabase Cloud Database.")
        except Exception as e:
            logger.error(f"[DB] Error inicializando cliente Supabase: {e}")
            _supabase_client = None
    return _supabase_client


# ─── SQLite Fallback (para desarrollo offline y testing) ───────────────────────

def get_connection() -> sqlite3.Connection:
    """Crea y devuelve una conexión a la base de datos SQLite."""
    os.makedirs(os.path.dirname(settings.DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    """Inicializa tablas locales si no se usa Supabase."""
    sb = get_supabase()
    if sb:
        return

    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ingredients (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(60) NOT NULL,
                category VARCHAR(30) NOT NULL DEFAULT 'other',
                quantity NUMERIC(10, 2),
                unit VARCHAR(30) DEFAULT 'units',
                expiration_date DATE,
                confidence NUMERIC(3, 2),
                source VARCHAR(10) NOT NULL DEFAULT 'ai',
                confirmed BOOLEAN NOT NULL DEFAULT 1,
                image_uri TEXT,
                notes TEXT,
                expiration_source VARCHAR(20) DEFAULT 'unknown',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ingredients_user_id ON ingredients(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ingredients_user_expiry ON ingredients(user_id, expiration_date);")

        # Verificar usuario demo en SQLite
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE email = ?", ("demo@foodai.com",))
        if not cursor.fetchone():
            from app.core.security import hash_password
            demo_hash = hash_password("123456")
            conn.execute(
                """
                INSERT INTO users (id, email, name, password_hash)
                VALUES (?, ?, ?, ?)
                """,
                ("usr-demo-1", "demo@foodai.com", "Chef Demo", demo_hash),
            )
        conn.commit()


# ─── Operaciones de Usuario (Supabase con fallback SQLite) ─────────────────────

def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    """Busca un usuario por su email."""
    clean_email = email.strip().lower()
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("users").select("*").eq("email", clean_email).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
            return None
        except Exception as err:
            logger.error(f"[DB] Error consultando usuario en Supabase: {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, email, name, password_hash, created_at, updated_at FROM users WHERE email = ?",
            (clean_email,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    """Busca un usuario por su ID."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("users").select("*").eq("id", user_id).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
            return None
        except Exception as err:
            logger.error(f"[DB] Error consultando usuario por ID en Supabase: {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, email, name, password_hash, created_at, updated_at FROM users WHERE id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_user(user_id: str, email: str, name: str, password_hash: str) -> Dict[str, Any]:
    """Crea e inserta un nuevo usuario."""
    clean_email = email.strip().lower()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sb = get_supabase()
    if sb:
        try:
            user_data = {
                "id": user_id,
                "email": clean_email,
                "name": name,
                "password_hash": password_hash,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            res = sb.table("users").insert(user_data).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
            return user_data
        except Exception as err:
            logger.error(f"[DB] Error insertando usuario en Supabase: {err}")
            raise

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (id, email, name, password_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, clean_email, name, password_hash, now_iso, now_iso),
        )
        conn.commit()
    return {
        "id": user_id,
        "email": clean_email,
        "name": name,
        "password_hash": password_hash,
        "created_at": now_iso,
        "updated_at": now_iso,
    }


# ─── Operaciones de Inventario (CRUD con persistencia y preservación de campos) ─

def _row_to_ingredient_dict(row: Any) -> Dict[str, Any]:
    """Mapea una fila SQLite o Supabase al formato canónico conservando todos los campos."""
    d = dict(row)
    # Soporta tanto camelCase como snake_case para el cliente
    d["expirationDate"] = d.get("expiration_date") or d.get("expirationDate")
    d["imageUri"] = d.get("image_uri") or d.get("imageUri")
    d["expirationSource"] = d.get("expiration_source") or d.get("expirationSource")
    d["userId"] = d.get("user_id") or d.get("userId")
    d["createdAt"] = d.get("created_at") or d.get("createdAt")
    d["updatedAt"] = d.get("updated_at") or d.get("updatedAt")
    if "confirmed" in d:
        d["confirmed"] = bool(d["confirmed"])
    return d


def get_inventory_by_user(user_id: str) -> List[Dict[str, Any]]:
    """Recupera todos los ingredientes del inventario de un usuario."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("ingredients").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
            return [_row_to_ingredient_dict(item) for item in (res.data or [])]
        except Exception as err:
            logger.error(f"[DB] Error consultando ingredientes en Supabase: {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, name, category, quantity, unit, expiration_date,
                   confidence, source, confirmed, image_uri, notes, expiration_source,
                   created_at, updated_at
            FROM ingredients
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
        return [_row_to_ingredient_dict(r) for r in rows]


def get_ingredient_by_id(ingredient_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Busca un ingrediente específico asegurando que pertenezca al usuario indicado."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("ingredients").select("*").eq("id", ingredient_id).eq("user_id", user_id).execute()
            if res.data and len(res.data) > 0:
                return _row_to_ingredient_dict(res.data[0])
            return None
        except Exception as err:
            logger.error(f"[DB] Error consultando ingrediente por ID en Supabase: {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, name, category, quantity, unit, expiration_date,
                   confidence, source, confirmed, image_uri, notes, expiration_source,
                   created_at, updated_at
            FROM ingredients
            WHERE id = ? AND user_id = ?
            """,
            (ingredient_id, user_id),
        )
        row = cursor.fetchone()
        return _row_to_ingredient_dict(row) if row else None


def create_ingredient(item: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Crea y persiste un alimento en la base de datos asegurando todos sus campos."""
    ing_id = item.get("id") or f"ing-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    record = {
        "id": ing_id,
        "user_id": user_id,
        "name": str(item.get("name", "")).strip(),
        "category": str(item.get("category", "other")).lower(),
        "quantity": item.get("quantity"),
        "unit": item.get("unit"),
        "expiration_date": item.get("expirationDate") or item.get("expiration_date"),
        "confidence": item.get("confidence"),
        "source": str(item.get("source", "manual")).lower(),
        "confirmed": bool(item.get("confirmed", True)),
        "image_uri": item.get("imageUri") or item.get("image_uri"),
        "notes": item.get("notes"),
        "expiration_source": item.get("expirationSource") or item.get("expiration_source", "unknown"),
        "created_at": item.get("created_at") or now_iso,
        "updated_at": now_iso,
    }

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("ingredients").insert(record).execute()
            if res.data and len(res.data) > 0:
                return _row_to_ingredient_dict(res.data[0])
            return _row_to_ingredient_dict(record)
        except Exception as err:
            logger.error(f"[DB] Error insertando ingrediente en Supabase: {err}")
            raise

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO ingredients (
                id, user_id, name, category, quantity, unit, expiration_date,
                confidence, source, confirmed, image_uri, notes, expiration_source,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["user_id"],
                record["name"],
                record["category"],
                record["quantity"],
                record["unit"],
                record["expiration_date"],
                record["confidence"],
                record["source"],
                1 if record["confirmed"] else 0,
                record["image_uri"],
                record["notes"],
                record["expiration_source"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        conn.commit()

    return _row_to_ingredient_dict(record)


def update_ingredient(ingredient_id: str, user_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Actualiza los campos especificados de un alimento y actualiza updated_at."""
    existing = get_ingredient_by_id(ingredient_id, user_id)
    if not existing:
        return None

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Mapeo de campos a actualizar
    field_mapping = {
        "name": updates.get("name"),
        "category": updates.get("category"),
        "quantity": updates.get("quantity") if "quantity" in updates else existing.get("quantity"),
        "unit": updates.get("unit") if "unit" in updates else existing.get("unit"),
        "expiration_date": updates.get("expirationDate") or updates.get("expiration_date") if ("expirationDate" in updates or "expiration_date" in updates) else existing.get("expiration_date"),
        "confidence": updates.get("confidence") if "confidence" in updates else existing.get("confidence"),
        "source": updates.get("source") if "source" in updates else existing.get("source"),
        "confirmed": updates.get("confirmed") if "confirmed" in updates else existing.get("confirmed"),
        "image_uri": updates.get("imageUri") or updates.get("image_uri") if ("imageUri" in updates or "image_uri" in updates) else existing.get("image_uri"),
        "notes": updates.get("notes") if "notes" in updates else existing.get("notes"),
        "expiration_source": updates.get("expirationSource") or updates.get("expiration_source") if ("expirationSource" in updates or "expiration_source" in updates) else existing.get("expiration_source"),
        "updated_at": now_iso,
    }

    # Conservar valores existentes si en updates viene None explícito no deseado
    payload = {}
    for k, v in field_mapping.items():
        if k in updates or (k == "expiration_date" and "expirationDate" in updates) or (k == "image_uri" and "imageUri" in updates) or (k == "expiration_source" and "expirationSource" in updates) or k == "updated_at":
            payload[k] = v

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("ingredients").update(payload).eq("id", ingredient_id).eq("user_id", user_id).execute()
            if res.data and len(res.data) > 0:
                return _row_to_ingredient_dict(res.data[0])
            return None
        except Exception as err:
            logger.error(f"[DB] Error actualizando ingrediente en Supabase: {err}")
            raise

    # SQLite
    set_clauses = [f"{k} = ?" for k in payload.keys()]
    values = list(payload.values())
    values.extend([ingredient_id, user_id])

    with get_connection() as conn:
        conn.execute(
            f"""
            UPDATE ingredients
            SET {', '.join(set_clauses)}
            WHERE id = ? AND user_id = ?
            """,
            values,
        )
        conn.commit()

    return get_ingredient_by_id(ingredient_id, user_id)


def delete_ingredient(ingredient_id: str, user_id: str) -> bool:
    """Elimina físicamente un alimento de la base de datos verificando pertenencia."""
    existing = get_ingredient_by_id(ingredient_id, user_id)
    if not existing:
        return False

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("ingredients").delete().eq("id", ingredient_id).eq("user_id", user_id).execute()
            return True
        except Exception as err:
            logger.error(f"[DB] Error eliminando ingrediente en Supabase: {err}")
            raise

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM ingredients WHERE id = ? AND user_id = ?",
            (ingredient_id, user_id),
        )
        conn.commit()
        return cursor.rowcount > 0


def delete_ingredients_batch(ingredient_ids: List[str], user_id: str) -> int:
    """Elimina múltiples alimentos del usuario de forma atómica."""
    if not ingredient_ids:
        return 0

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("ingredients").delete().in_("id", ingredient_ids).eq("user_id", user_id).execute()
            return len(res.data) if res.data else 0
        except Exception as err:
            logger.error(f"[DB] Error en borrado masivo en Supabase: {err}")
            raise

    with get_connection() as conn:
        placeholders = ",".join(["?"] * len(ingredient_ids))
        cursor = conn.cursor()
        cursor.execute(
            f"DELETE FROM ingredients WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *ingredient_ids],
        )
        conn.commit()
        return cursor.rowcount


def reset_db():
    """Limpia todos los usuarios e ingredientes excepto el usuario demo para pruebas aisladas."""
    sb = get_supabase()
    if sb:
        try:
            sb.table("ingredients").delete().neq("id", "none").execute()
            sb.table("users").delete().neq("email", "demo@foodai.com").execute()
            return
        except Exception as err:
            logger.error(f"[DB] Error reseteando base de datos en Supabase: {err}")

    with get_connection() as conn:
        conn.execute("DELETE FROM ingredients;")
        conn.execute("DELETE FROM users WHERE email != 'demo@foodai.com';")
        conn.commit()


# Inicialización al importar el módulo
init_db()
