import os
import sqlite3
import logging
import datetime
import json
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

import threading

_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Devuelve la conexión SQLite reutilizable para el hilo actual evitando overhead de reconexión."""
    conn = getattr(_local, "connection", None)
    if conn is None:
        os.makedirs(os.path.dirname(settings.DATABASE_PATH), exist_ok=True)
        conn = sqlite3.connect(settings.DATABASE_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        _local.connection = conn
    return conn


def init_db():
    """Inicializa tablas locales SQLite para garantizar disponibilidad offline y fallback resiliente."""
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

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                title VARCHAR(120) NOT NULL,
                description TEXT,
                prep_time_minutes INTEGER DEFAULT 30,
                servings INTEGER DEFAULT 2,
                difficulty VARCHAR(20) DEFAULT 'easy',
                match_score INTEGER DEFAULT 90,
                available_ingredients TEXT,
                missing_ingredients TEXT,
                steps TEXT,
                is_saved BOOLEAN NOT NULL DEFAULT 1,
                is_prepared BOOLEAN NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recipes_user_id ON recipes(user_id);")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shopping_items (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                quantity NUMERIC(10, 2),
                unit VARCHAR(30) DEFAULT 'units',
                category VARCHAR(30) NOT NULL DEFAULT 'other',
                is_bought BOOLEAN NOT NULL DEFAULT 0,
                recipe_source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shopping_items_user_id ON shopping_items(user_id);")

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

    # SQLite: actualizar directamente y retornar merge en memoria sin re-consultar la base de datos
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

    merged = dict(existing)
    merged.update(payload)
    return _row_to_ingredient_dict(merged)


def delete_ingredient(ingredient_id: str, user_id: str) -> bool:
    """Elimina físicamente un alimento de la base de datos verificando pertenencia sin SELECT previo."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("ingredients").delete().eq("id", ingredient_id).eq("user_id", user_id).execute()
            return len(res.data) > 0 if res.data is not None else True
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
            sb.table("shopping_items").delete().neq("id", "none").execute()
            sb.table("recipes").delete().neq("id", "none").execute()
            sb.table("ingredients").delete().neq("id", "none").execute()
            sb.table("users").delete().neq("email", "demo@foodai.com").execute()
            return
        except Exception as err:
            logger.error(f"[DB] Error reseteando base de datos en Supabase: {err}")

    with get_connection() as conn:
        conn.execute("DELETE FROM shopping_items;")
        conn.execute("DELETE FROM recipes;")
        conn.execute("DELETE FROM ingredients;")
        conn.execute("DELETE FROM users WHERE email != 'demo@foodai.com';")
        conn.commit()


# ─── Operaciones de Recetas Guardadas (Supabase Cloud + SQLite Fallback) ──────

def _row_to_recipe_dict(row: Any) -> Dict[str, Any]:
    """Mapea una fila de base de datos a formato Recipe canónico parseando campos serializados."""
    d = dict(row)
    d["prepTimeMinutes"] = d.get("prep_time_minutes") if "prep_time_minutes" in d else d.get("prepTimeMinutes", 30)
    d["matchScore"] = d.get("match_score") if "match_score" in d else d.get("matchScore", 90)
    d["isSaved"] = bool(d.get("is_saved", d.get("isSaved", True)))
    d["isPrepared"] = bool(d.get("is_prepared", d.get("isPrepared", False)))

    # Parsear colecciones JSON
    for key, alt in [("availableIngredients", "available_ingredients"), ("missingIngredients", "missing_ingredients"), ("steps", "steps")]:
        raw = d.get(key) if key in d else d.get(alt)
        if isinstance(raw, str):
            try:
                d[key] = json.loads(raw)
            except Exception:
                d[key] = []
        elif isinstance(raw, list):
            d[key] = raw
        else:
            d[key] = []

    d["userId"] = d.get("user_id") or d.get("userId")
    d["createdAt"] = d.get("created_at") or d.get("createdAt")
    d["updatedAt"] = d.get("updated_at") or d.get("updatedAt")
    return d


def get_saved_recipes_by_user(user_id: str) -> List[Dict[str, Any]]:
    """Recupera todas las recetas guardadas por un usuario."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("recipes").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
            if res.data is not None:
                return [_row_to_recipe_dict(item) for item in res.data]
        except Exception as err:
            logger.warning(f"[DB] Error consultando recetas en Supabase (usando fallback SQLite): {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, title, description, prep_time_minutes, servings,
                   difficulty, match_score, available_ingredients, missing_ingredients,
                   steps, is_saved, is_prepared, created_at, updated_at
            FROM recipes
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
        return [_row_to_recipe_dict(r) for r in rows]


def get_saved_recipe_by_id(recipe_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Busca una receta específica perteneciente al usuario."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("recipes").select("*").eq("id", recipe_id).eq("user_id", user_id).execute()
            if res.data and len(res.data) > 0:
                return _row_to_recipe_dict(res.data[0])
        except Exception as err:
            logger.warning(f"[DB] Error consultando receta en Supabase: {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM recipes WHERE id = ? AND user_id = ?",
            (recipe_id, user_id),
        )
        row = cursor.fetchone()
        return _row_to_recipe_dict(row) if row else None


def save_recipe(recipe: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Guarda o actualiza una receta en la base de datos persistente."""
    rec_id = recipe.get("id") or f"rec-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    avail = recipe.get("availableIngredients") or recipe.get("available_ingredients") or []
    missing = recipe.get("missingIngredients") or recipe.get("missing_ingredients") or []
    steps = recipe.get("steps") or []

    avail_str = json.dumps(avail) if isinstance(avail, (list, dict)) else str(avail)
    missing_str = json.dumps(missing) if isinstance(missing, (list, dict)) else str(missing)
    steps_str = json.dumps(steps) if isinstance(steps, (list, dict)) else str(steps)

    record = {
        "id": rec_id,
        "user_id": user_id,
        "title": str(recipe.get("title", "")).strip(),
        "description": str(recipe.get("description", "")).strip(),
        "prep_time_minutes": int(recipe.get("prepTimeMinutes") or recipe.get("prep_time_minutes") or 30),
        "servings": int(recipe.get("servings", 2)),
        "difficulty": str(recipe.get("difficulty", "easy")),
        "match_score": int(recipe.get("matchScore") or recipe.get("match_score") or 90),
        "available_ingredients": avail_str,
        "missing_ingredients": missing_str,
        "steps": steps_str,
        "is_saved": 1 if recipe.get("isSaved", True) else 0,
        "is_prepared": 1 if recipe.get("isPrepared", False) else 0,
        "created_at": recipe.get("createdAt") or recipe.get("created_at") or now_iso,
        "updated_at": now_iso,
    }

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("recipes").upsert(record).execute()
            if res.data and len(res.data) > 0:
                return _row_to_recipe_dict(res.data[0])
            return _row_to_recipe_dict(record)
        except Exception as err:
            logger.warning(f"[DB] Error guardando receta en Supabase (fallback SQLite): {err}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO recipes (
                id, user_id, title, description, prep_time_minutes, servings,
                difficulty, match_score, available_ingredients, missing_ingredients,
                steps, is_saved, is_prepared, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["user_id"],
                record["title"],
                record["description"],
                record["prep_time_minutes"],
                record["servings"],
                record["difficulty"],
                record["match_score"],
                record["available_ingredients"],
                record["missing_ingredients"],
                record["steps"],
                record["is_saved"],
                record["is_prepared"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        conn.commit()

    return _row_to_recipe_dict(record)


def delete_saved_recipe(recipe_id: str, user_id: str) -> bool:
    """Elimina una receta guardada de la base de datos."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("recipes").delete().eq("id", recipe_id).eq("user_id", user_id).execute()
            if res.data is not None and len(res.data) > 0:
                return True
        except Exception as err:
            logger.warning(f"[DB] Error eliminando receta en Supabase (fallback SQLite): {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM recipes WHERE id = ? AND user_id = ?", (recipe_id, user_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_saved_recipes_batch(recipe_ids: List[str], user_id: str) -> int:
    """Elimina múltiples recetas guardadas de forma atómica."""
    if not recipe_ids:
        return 0

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("recipes").delete().in_("id", recipe_ids).eq("user_id", user_id).execute()
            if res.data is not None:
                return len(res.data)
        except Exception as err:
            logger.warning(f"[DB] Error eliminando lote de recetas en Supabase: {err}")

    with get_connection() as conn:
        placeholders = ",".join(["?"] * len(recipe_ids))
        cursor = conn.cursor()
        cursor.execute(
            f"DELETE FROM recipes WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *recipe_ids],
        )
        conn.commit()
        return cursor.rowcount


# ─── Operaciones de Lista de Compras (Supabase Cloud + SQLite Fallback) ───────

def _row_to_shopping_dict(row: Any) -> Dict[str, Any]:
    """Mapea una fila de base de datos a formato ShoppingItem canónico."""
    d = dict(row)
    d["isBought"] = bool(d.get("is_bought", d.get("isBought", False)))
    d["recipeSource"] = d.get("recipe_source") or d.get("recipeSource")
    d["userId"] = d.get("user_id") or d.get("userId")
    d["createdAt"] = d.get("created_at") or d.get("createdAt")
    d["updatedAt"] = d.get("updated_at") or d.get("updatedAt")
    return d


def get_shopping_items_by_user(user_id: str) -> List[Dict[str, Any]]:
    """Recupera todos los artículos de la lista de compras de un usuario."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("shopping_items").select("*").eq("user_id", user_id).order("created_at", desc=False).execute()
            if res.data is not None:
                return [_row_to_shopping_dict(item) for item in res.data]
        except Exception as err:
            logger.warning(f"[DB] Error consultando lista de compras en Supabase (fallback SQLite): {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, name, quantity, unit, category, is_bought, recipe_source, created_at, updated_at
            FROM shopping_items
            WHERE user_id = ?
            ORDER BY created_at ASC
            """,
            (user_id,),
        )
        rows = cursor.fetchall()
        return [_row_to_shopping_dict(r) for r in rows]


def get_shopping_item_by_id(item_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Busca un artículo de compra perteneciente al usuario."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("shopping_items").select("*").eq("id", item_id).eq("user_id", user_id).execute()
            if res.data and len(res.data) > 0:
                return _row_to_shopping_dict(res.data[0])
        except Exception as err:
            logger.warning(f"[DB] Error consultando artículo de compras en Supabase: {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM shopping_items WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        )
        row = cursor.fetchone()
        return _row_to_shopping_dict(row) if row else None


def create_shopping_item(item: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Crea y persiste un nuevo artículo en la lista de compras."""
    item_id = item.get("id") or f"shop-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    record = {
        "id": item_id,
        "user_id": user_id,
        "name": str(item.get("name", "")).strip(),
        "quantity": item.get("quantity"),
        "unit": str(item.get("unit", "units")),
        "category": str(item.get("category", "other")).lower(),
        "is_bought": 1 if item.get("isBought", item.get("is_bought", False)) else 0,
        "recipe_source": item.get("recipeSource") or item.get("recipe_source"),
        "created_at": item.get("createdAt") or item.get("created_at") or now_iso,
        "updated_at": now_iso,
    }

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("shopping_items").insert(record).execute()
            if res.data and len(res.data) > 0:
                return _row_to_shopping_dict(res.data[0])
            return _row_to_shopping_dict(record)
        except Exception as err:
            logger.warning(f"[DB] Error insertando artículo en Supabase (fallback SQLite): {err}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO shopping_items (
                id, user_id, name, quantity, unit, category, is_bought, recipe_source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["user_id"],
                record["name"],
                record["quantity"],
                record["unit"],
                record["category"],
                record["is_bought"],
                record["recipe_source"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        conn.commit()

    return _row_to_shopping_dict(record)


def update_shopping_item(item_id: str, user_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Actualiza campos de un artículo de compras (ej. marcar comprado o cambiar cantidad)."""
    existing = get_shopping_item_by_id(item_id, user_id)
    if not existing:
        return None

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload: Dict[str, Any] = {"updated_at": now_iso}

    if "name" in updates:
        payload["name"] = updates["name"]
    if "quantity" in updates:
        payload["quantity"] = updates["quantity"]
    if "unit" in updates:
        payload["unit"] = updates["unit"]
    if "category" in updates:
        payload["category"] = updates["category"]
    if "isBought" in updates or "is_bought" in updates:
        val = updates.get("isBought") if "isBought" in updates else updates.get("is_bought")
        payload["is_bought"] = 1 if val else 0
    if "recipeSource" in updates or "recipe_source" in updates:
        payload["recipe_source"] = updates.get("recipeSource") or updates.get("recipe_source")

    sb = get_supabase()
    if sb:
        try:
            res = sb.table("shopping_items").update(payload).eq("id", item_id).eq("user_id", user_id).execute()
            if res.data and len(res.data) > 0:
                return _row_to_shopping_dict(res.data[0])
        except Exception as err:
            logger.warning(f"[DB] Error actualizando en Supabase (fallback SQLite): {err}")

    set_clauses = [f"{k} = ?" for k in payload.keys()]
    values = list(payload.values())
    values.extend([item_id, user_id])

    with get_connection() as conn:
        conn.execute(
            f"UPDATE shopping_items SET {', '.join(set_clauses)} WHERE id = ? AND user_id = ?",
            values,
        )
        conn.commit()

    merged = dict(existing)
    merged.update(payload)
    return _row_to_shopping_dict(merged)


def delete_shopping_item(item_id: str, user_id: str) -> bool:
    """Elimina físicamente un artículo de la lista de compras."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("shopping_items").delete().eq("id", item_id).eq("user_id", user_id).execute()
            if res.data is not None and len(res.data) > 0:
                return True
        except Exception as err:
            logger.warning(f"[DB] Error eliminando en Supabase (fallback SQLite): {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM shopping_items WHERE id = ? AND user_id = ?", (item_id, user_id))
        conn.commit()
        return cursor.rowcount > 0


def delete_bought_shopping_items(user_id: str) -> int:
    """Elimina todos los artículos marcados como comprados del usuario."""
    sb = get_supabase()
    if sb:
        try:
            res = sb.table("shopping_items").delete().eq("user_id", user_id).eq("is_bought", 1).execute()
            if res.data is not None:
                return len(res.data)
        except Exception as err:
            logger.warning(f"[DB] Error eliminando comprados en Supabase: {err}")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM shopping_items WHERE user_id = ? AND is_bought = 1", (user_id,))
        conn.commit()
        return cursor.rowcount


def batch_create_shopping_items(items: List[Dict[str, Any]], user_id: str) -> List[Dict[str, Any]]:
    """Inserta múltiples artículos en la lista de compras de forma atómica."""
    created = []
    for item in items:
        created.append(create_shopping_item(item, user_id=user_id))
    return created
