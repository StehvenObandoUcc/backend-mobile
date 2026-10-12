"""Parte 3 · Caché persistente de fotos aprobadas por plato (tabla dish_photos). SQLite local, sin red."""
import asyncio
import os

import pytest

from app.core import db
from app.core.config import settings
from app.services import recipe_photo
from app.services.recipe_photo import PHOTO_RULES_VERSION, _dish_key, attach_photos
from tests.test_recipe_photo import DOWNLOAD_URL, _FakeClient, _photo, _recipe, _vision

URI = "https://images.unsplash.com/stored-regular"
CREDIT = {"source": "unsplash", "name": "Ana Foto", "profileUrl": "https://unsplash.com/@ana?utm_source=x",
          "sourceUrl": "https://unsplash.com/photos/abc?utm_source=x"}
LOCATION = "https://api.unsplash.com/photos/stored/download"


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    recipe_photo._reset_state_for_tests()
    monkeypatch.setattr(settings, "UNSPLASH_ACCESS_KEY", "u-key", raising=False)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "d-key", raising=False)
    monkeypatch.setattr(settings, "VISION_DAILY_CAP", 50, raising=False)
    monkeypatch.setattr(settings, "PHOTO_TIME_BUDGET_SECONDS", 5.0, raising=False)
    yield
    recipe_photo._reset_state_for_tests()


def _store(key="tomato scrambled eggs", version=PHOTO_RULES_VERSION, uri=URI, location=LOCATION):
    return db.save_dish_photo(key, uri, CREDIT, location, version)


# ── db.py ──
def test_dish_photo_roundtrip_and_replace():
    assert db.get_dish_photo("tomato scrambled eggs", 2) is None
    assert _store() is True
    got = db.get_dish_photo("tomato scrambled eggs", 2)
    assert got == {"imageUri": URI, "imageCredit": CREDIT, "downloadLocation": LOCATION}
    assert _store(uri="https://images.unsplash.com/newer-regular", version=3) is True  # misma clave: reemplaza
    assert db.get_dish_photo("tomato scrambled eggs", 2) is None
    assert db.get_dish_photo("tomato scrambled eggs", 3)["imageUri"].endswith("newer-regular")


def test_dish_photo_other_version_is_ignored():
    _store(version=1)
    assert db.get_dish_photo("tomato scrambled eggs", 2) is None


@pytest.mark.parametrize("uri", ["http://images.unsplash.com/x", "https://images.unsplash.com.evil.com/x", "https://evil.com/x", None])
def test_save_dish_photo_rejects_invalid_image_uri(uri):
    assert db.save_dish_photo("dish", uri, CREDIT, LOCATION, 2) is False
    assert db.get_dish_photo("dish", 2) is None


def test_save_dish_photo_rejects_invalid_credit_and_drops_foreign_download_location():
    assert db.save_dish_photo("dish", URI, {"name": 5, "profileUrl": "https://evil.com"}, LOCATION, 2) is False
    assert db.save_dish_photo("dish", URI, CREDIT, "https://evil.com/download", 2) is True
    assert db.get_dish_photo("dish", 2)["downloadLocation"] is None


def test_get_dish_photo_revalidates_what_is_in_the_table():
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO dish_photos (dish_key, image_uri, image_credit, download_location, rules_version) VALUES (?,?,?,?,?)",
            ("evil", "https://evil.com/x.jpg", "{\"name\": \"x\"}", None, 2),
        )
        conn.commit()
    assert db.get_dish_photo("evil", 2) is None


def test_db_failure_returns_none_and_does_not_save(monkeypatch):
    def boom():
        raise RuntimeError("base caída")
    monkeypatch.setattr(db, "get_connection", boom)
    assert db.get_dish_photo("dish", 2) is None
    assert db.save_dish_photo("dish", URI, CREDIT, LOCATION, 2) is False


# ── recipe_photo.py ──
def test_dish_key_normalization():
    assert _dish_key("  Tomato   Scrambled-Eggs!! ") == "tomato scrambled eggs"
    assert _dish_key("Crème brûlée") == "creme brulee" and _dish_key(None) == "" and _dish_key("!!!") == ""


def test_table_hit_skips_unsplash_vision_and_user_quota_and_pings_download():
    _store()
    client = _FakeClient(default=[_photo()], vision=[])
    r = _recipe()
    reasons = []
    stats = asyncio.run(attach_photos([r], http_client=client, reasons=reasons, user_id="u1"))
    assert r.imageUri == URI and r.imageCredit == CREDIT and reasons == ["aprobada"]
    assert client.searches == [] and client.vision_payloads == []
    assert stats["db_cache_hits"] == 1 and stats["searches"] == 0 and stats["vision_calls"] == 0 and stats["cache_hits"] == 0
    assert client.downloads == [DOWNLOAD_URL.replace("p1", "stored")]
    assert recipe_photo._vision_state["count"] == 0
    # Rellena la caché en memoria: la segunda vez ya no consulta la tabla
    assert len(recipe_photo._POSITIVE_CACHE) == 1
    db.reset_db()
    r2 = _recipe()
    stats2 = asyncio.run(attach_photos([r2], http_client=client, user_id="u1"))
    assert r2.imageUri == URI and stats2["cache_hits"] == 1 and stats2["db_cache_hits"] == 0


def test_table_entry_of_other_rules_version_is_ignored():
    _store(version=PHOTO_RULES_VERSION - 1)
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    r = _recipe()
    stats = asyncio.run(attach_photos([r], http_client=client))
    assert stats["db_cache_hits"] == 0 and stats["searches"] == 1 and stats["vision_calls"] == 1
    assert r.imageUri == "https://images.unsplash.com/p1-regular"
    assert db.get_dish_photo("tomato scrambled eggs", PHOTO_RULES_VERSION)["imageUri"].endswith("p1-regular")  # se reemplazó


def test_table_hit_already_used_in_batch_follows_normal_flow():
    _store()
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    r = _recipe()
    stats = asyncio.run(attach_photos([r], http_client=client, used_ids={URI}))
    assert stats["db_cache_hits"] == 0 and stats["searches"] == 1 and r.imageUri == "https://images.unsplash.com/p1-regular"


def test_candidate_equal_to_a_table_photo_used_in_batch_is_not_repeated():
    client = _FakeClient(default=[_photo("stored", alt="eggs with tomato")], vision=[_vision()])
    r = _recipe(dish="other dish")
    asyncio.run(attach_photos([r], http_client=client, used_ids={URI}))  # misma foto (por URL) ya usada
    assert r.imageUri is None


def test_db_error_does_not_break_photo_flow(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("base caída")
    monkeypatch.setattr(recipe_photo, "get_dish_photo", boom)
    monkeypatch.setattr(recipe_photo, "save_dish_photo", boom)
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    r = _recipe()
    reasons = []
    asyncio.run(attach_photos([r], http_client=client, reasons=reasons))
    assert r.imageUri is not None and reasons == ["aprobada"]


def test_new_approval_is_saved_with_credit_and_download_location():
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    asyncio.run(attach_photos([_recipe(dish="Tomato Scrambled Eggs!")], http_client=client))
    got = db.get_dish_photo("tomato scrambled eggs", PHOTO_RULES_VERSION)
    assert got["imageUri"] == "https://images.unsplash.com/p1-regular"
    assert got["imageCredit"]["name"] == "Ana Foto" and got["downloadLocation"] == DOWNLOAD_URL


def test_rejections_and_recipes_without_dish_name_are_not_saved():
    client = _FakeClient(default=[_photo()], vision=[_vision(named_visible=False)])
    asyncio.run(attach_photos([_recipe()], http_client=client))
    assert db.get_dish_photo("tomato scrambled eggs", PHOTO_RULES_VERSION) is None
    recipe_photo._reset_state_for_tests()
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    asyncio.run(attach_photos([_recipe(dish="")], http_client=client))
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM dish_photos").fetchone()[0] == 0


def test_migration_04_has_rls_enabled_and_forced_without_policies():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "04_dish_photos.sql")
    sql = open(path, encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS public.dish_photos" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql and "FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY" not in sql
    assert "dish_key          text PRIMARY KEY" in sql and "rules_version" in sql


def test_live_search_photo_is_not_repeated_by_a_later_table_hit_in_the_same_batch():
    _store(key="dish b", uri="https://images.unsplash.com/p1-regular")
    client = _FakeClient(default=[_photo("p1", alt="eggs with tomato")], vision=[_vision()])
    used = set()
    r1, r2 = _recipe(dish="dish a"), _recipe(dish="dish b")
    asyncio.run(attach_photos([r1], http_client=client, used_ids=used))  # receta 1: búsqueda en vivo
    assert r1.imageUri == "https://images.unsplash.com/p1-regular" and "https://images.unsplash.com/p1-regular" in used
    stats = asyncio.run(attach_photos([r2], http_client=client, used_ids=used))  # receta 2: la tabla trae la misma URL
    assert stats["db_cache_hits"] == 0 and r2.imageUri is None  # no se repite; sigue al flujo normal y no hay otra candidata


def test_negative_cache_keeps_the_vision_reason():
    client = _FakeClient(default=[_photo()], vision=[_vision(named_visible=False, reason="no se ve")])
    asyncio.run(attach_photos([_recipe()], http_client=client))
    reasons = []
    asyncio.run(attach_photos([_recipe()], http_client=client, reasons=reasons))
    assert reasons == ["vision_no: no se ve"] and len(client.searches) == 1
