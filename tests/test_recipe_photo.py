"""Fase 3a · Foto real verificada de cada receta (sin red real: cliente falso que enruta por URL)."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1 import recipes as recipes_api
from app.core.config import settings
from app.schemas.recipe import RecipeGenerateRequest, RecipeResponse
from app.services import ai_recipe_service, recipe_photo
from app.services.ai_recipe_service import AIRecipeService
from app.services.recipe_photo import attach_photos

SEARCH_URL = "https://api.unsplash.com/search/photos"
DOWNLOAD_URL = "https://api.unsplash.com/photos/p1/download"


class _Resp:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data if data is not None else {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


def _vision(is_food=True, matches=True):
    payload = {"is_food_dish": is_food, "matches": matches, "reason": "prueba"}
    return _Resp(200, {"choices": [{"message": {"content": json.dumps(payload)}}]})


def _photo(pid="p1", alt="scrambled eggs with tomato on a plate", desc=None, tags=None):
    return {
        "id": pid,
        "description": desc,
        "alt_description": alt,
        "tags": [{"title": t} for t in (tags or [])],
        "urls": {"regular": f"https://images.unsplash.com/{pid}-regular", "small": f"https://images.unsplash.com/{pid}-small"},
        "links": {"download_location": f"https://api.unsplash.com/photos/{pid}/download"},
        "user": {"name": "Ana Foto", "links": {"html": "https://unsplash.com/@anafoto"}},
    }


class _FakeClient:
    """Enruta por URL: búsqueda de Unsplash, aviso de descarga y visión de DeepSeek."""

    def __init__(self, by_query=None, default=None, vision=None, search_status=200, search_headers=None, delay=0.0):
        self.by_query = by_query or {}
        self.default = default if default is not None else []
        self.vision = vision  # lista de respuestas, o una excepción a lanzar
        self.search_status = search_status
        self.search_headers = search_headers or {}
        self.delay = delay
        self.searches = []   # parámetros de cada búsqueda
        self.search_headers_seen = []
        self.downloads = []
        self.vision_payloads = []

    async def get(self, url, headers=None, params=None, timeout=None):
        if self.delay:
            await asyncio.sleep(self.delay)
        if url == SEARCH_URL:
            self.searches.append(params)
            self.search_headers_seen.append(headers)
            if self.search_status != 200:
                return _Resp(self.search_status, {}, self.search_headers)
            results = self.by_query.get(params["query"], self.default)
            return _Resp(200, {"results": results}, self.search_headers)
        self.downloads.append(url)
        return _Resp(200, {})

    async def post(self, url, headers=None, json=None, timeout=None):
        self.vision_payloads.append(json)
        if isinstance(self.vision, Exception):
            raise self.vision
        return self.vision.pop(0)


def _recipe(dish="tomato scrambled eggs", mains=("egg", "tomato"), title="Huevos con tomate"):
    return RecipeResponse(
        id="r1", title=title, description="Desayuno rápido", prepTimeMinutes=15, servings=2,
        difficulty="easy", matchScore=90, dishNameEn=dish, mainIngredientsEn=list(mains),
    )


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    recipe_photo._reset_state_for_tests()
    monkeypatch.setattr(settings, "UNSPLASH_ACCESS_KEY", "u-key", raising=False)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "d-key", raising=False)
    monkeypatch.setattr(settings, "VISION_DAILY_CAP", 50, raising=False)
    monkeypatch.setattr(settings, "PHOTO_TIME_BUDGET_SECONDS", 5.0, raising=False)
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", False, raising=False)
    yield
    recipe_photo._reset_state_for_tests()


def _run(recipes, client):
    return asyncio.run(attach_photos(recipes, http_client=client))


# ── Aprobación con visión ──
def test_vision_approval_sets_image_credit_and_pings_download():
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    r = _recipe()
    stats = _run([r], client)
    assert r.imageUri == "https://images.unsplash.com/p1-regular"
    assert r.imageCredit == {
        "source": "unsplash",
        "name": "Ana Foto",
        "profileUrl": "https://unsplash.com/@anafoto?utm_source=food_ai_assistant&utm_medium=referral",
        "sourceUrl": "https://unsplash.com/?utm_source=food_ai_assistant&utm_medium=referral",
    }
    assert client.downloads == [DOWNLOAD_URL]
    assert stats["vision_calls"] == 1 and stats["approved_vision"] == 1 and stats["no_photo"] == 0
    # Parámetros y cabeceras de la búsqueda, y formato de la llamada de visión
    assert client.searches[0] == {"query": "tomato scrambled eggs", "per_page": 5, "orientation": "landscape", "content_filter": "high"}
    assert client.search_headers_seen[0] == {"Authorization": "Client-ID u-key", "Accept-Version": "v1"}
    payload = client.vision_payloads[0]
    assert payload["max_tokens"] == 150 and payload["temperature"] == 0
    assert payload["response_format"] == {"type": "json_object"}
    image_part = payload["messages"][0]["content"][1]
    assert image_part == {"type": "image_url", "image_url": {"url": "https://images.unsplash.com/p1-small", "detail": "low"}}


def test_vision_says_no_leaves_recipe_without_photo_and_negative_cache_avoids_second_search():
    client = _FakeClient(default=[_photo()], vision=[_vision(matches=False)])
    r1 = _recipe()
    stats = _run([r1], client)
    assert r1.imageUri is None and stats["vision_calls"] == 1 and stats["no_photo"] == 1
    assert client.downloads == []
    r2 = _recipe()
    stats2 = _run([r2], client)
    assert r2.imageUri is None
    assert len(client.searches) == 1 and len(client.vision_payloads) == 1  # ni otra búsqueda ni otra visión
    assert stats2["cache_hits"] == 1 and stats2["searches"] == 0


def test_positive_cache_avoids_second_search():
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    _run([_recipe()], client)
    r2 = _recipe(title="Otra versión")
    stats = _run([r2], client)
    assert r2.imageUri == "https://images.unsplash.com/p1-regular"
    assert len(client.searches) == 1 and len(client.vision_payloads) == 1
    assert stats["cache_hits"] == 1 and stats["searches"] == 0 and stats["no_photo"] == 0


# ── Filtro de texto ──
def test_text_filter_discards_candidate_without_protagonists():
    client = _FakeClient(default=[_photo(alt="a bowl of pasta with basil")], vision=[])
    r = _recipe(dish="")  # sin dishNameEn: solo la consulta (b)
    stats = _run([r], client)
    assert r.imageUri is None and stats["vision_calls"] == 0 and client.vision_payloads == []


def test_text_filter_discards_contradicting_protein():
    bad = _photo("bad", alt="eggs and tomato with crispy bacon")
    client = _FakeClient(default=[bad], vision=[])
    r = _recipe()
    _run([r], client)
    assert r.imageUri is None and client.vision_payloads == []


def test_text_filter_allows_protein_that_is_a_protagonist_and_picks_highest_score():
    weak = _photo("weak", alt="grilled chicken on a board")
    strong = _photo("strong", alt="chicken with rice and lemon", tags=["rice"])
    client = _FakeClient(default=[weak, strong], vision=[_vision()])
    r = _recipe(dish="lemon chicken rice", mains=("chicken", "rice"))
    _run([r], client)
    assert r.imageUri == "https://images.unsplash.com/strong-regular"


def test_text_filter_accepts_plurals_and_accents():
    client = _FakeClient(default=[_photo(alt="Fresh Tomatoes and Eggs")], vision=[_vision()])
    r = _recipe()
    _run([r], client)
    assert r.imageUri is not None


# ── Solo texto ──
def test_vision_cap_exhausted_uses_text_rule(monkeypatch):
    monkeypatch.setattr(settings, "VISION_DAILY_CAP", 0, raising=False)
    client = _FakeClient(default=[_photo(alt="eggs with tomato")], vision=[])
    ok = _recipe()
    stats = _run([ok], client)
    assert ok.imageUri is not None and stats["approved_text"] == 1 and stats["vision_calls"] == 0

    recipe_photo._reset_state_for_tests()
    one_match = _FakeClient(default=[_photo(alt="scrambled eggs on toast")], vision=[])
    weak = _recipe()
    _run([weak], one_match)
    assert weak.imageUri is None  # 1 coincidencia con 2 protagonistas: se rechaza


def test_text_rule_single_protagonist_needs_one_match(monkeypatch):
    monkeypatch.setattr(settings, "VISION_DAILY_CAP", 0, raising=False)
    client = _FakeClient(default=[_photo(alt="a ripe avocado")], vision=[])
    r = _recipe(dish="avocado toast", mains=("avocado",))
    _run([r], client)
    assert r.imageUri is not None


def test_vision_failure_falls_back_to_text_rule_and_counts_toward_cap():
    client = _FakeClient(default=[_photo(alt="eggs with tomato")], vision=RuntimeError("falló"))
    r = _recipe()
    stats = _run([r], client)
    assert r.imageUri is not None
    assert stats["vision_calls"] == 1 and stats["approved_text"] == 1 and stats["approved_vision"] == 0
    assert recipe_photo._vision_state["count"] == 1


def test_no_deepseek_key_uses_text_rule(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "", raising=False)
    client = _FakeClient(default=[_photo(alt="eggs with tomato")], vision=[])
    r = _recipe()
    stats = _run([r], client)
    assert r.imageUri is not None and stats["approved_text"] == 1 and client.vision_payloads == []


# ── Sin clave, bloqueo, tiempo ──
def test_no_unsplash_key_makes_zero_calls(monkeypatch):
    monkeypatch.setattr(settings, "UNSPLASH_ACCESS_KEY", "", raising=False)
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    r = _recipe()
    stats = _run([r], client)
    assert r.imageUri is None and client.searches == [] and client.downloads == [] and client.vision_payloads == []
    assert stats["no_photo"] == 1


def test_recipe_without_main_ingredients_makes_no_calls():
    client = _FakeClient(default=[_photo()], vision=[_vision()])
    r = _recipe(mains=())
    _run([r], client)
    assert r.imageUri is None and client.searches == []


@pytest.mark.parametrize("code", [401, 403, 429])
def test_unsplash_error_blocks_without_retry(code):
    client = _FakeClient(default=[_photo()], vision=[_vision()], search_status=code)
    r1 = _recipe()
    _run([r1], client)
    assert r1.imageUri is None
    assert len(client.searches) == 1  # sin reintento: ni siquiera la consulta (b)
    r2 = _recipe(dish="other dish", mains=("rice", "bean"))
    _run([r2], client)
    assert r2.imageUri is None and len(client.searches) == 1  # bloqueado: la siguiente receta no busca


def test_rate_limit_header_zero_blocks_following_searches():
    client = _FakeClient(default=[_photo()], vision=[_vision()], search_headers={"X-Ratelimit-Remaining": "0"})
    r1 = _recipe()
    _run([r1], client)
    assert r1.imageUri is not None  # esta respuesta sí sirvió
    r2 = _recipe(dish="other dish", mains=("rice", "bean"))
    _run([r2], client)
    assert r2.imageUri is None and len(client.searches) == 1


def test_time_budget_exhausted_returns_without_exception_or_photo(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_TIME_BUDGET_SECONDS", 0.05, raising=False)
    client = _FakeClient(default=[_photo()], vision=[_vision()], delay=1.0)
    r = _recipe()
    stats = _run([r], client)
    assert r.imageUri is None and stats["no_photo"] == 1


def test_internal_error_per_recipe_leaves_it_without_photo():
    class _Boom(_FakeClient):
        async def get(self, *a, **k):
            raise RuntimeError("boom")

    r = _recipe()
    stats = _run([r], _Boom(vision=[]))
    assert r.imageUri is None and stats["no_photo"] == 1


# ── Escalera de consultas ──
def test_ladder_falls_to_second_query_and_never_exceeds_two_searches():
    good = _photo("g", alt="egg and tomato dish")
    client = _FakeClient(
        by_query={"tomato scrambled eggs": [_photo("x", alt="a sunset over the sea")], "egg tomato dish": [good]},
        vision=[_vision()],
    )
    r = _recipe()
    stats = _run([r], client)
    assert [s["query"] for s in client.searches] == ["tomato scrambled eggs", "egg tomato dish"]
    assert r.imageUri == "https://images.unsplash.com/g-regular" and stats["searches"] == 2

    recipe_photo._reset_state_for_tests()
    empty = _FakeClient(default=[_photo("x", alt="a sunset over the sea")], vision=[])
    r2 = _recipe()
    _run([r2], empty)
    assert len(empty.searches) == 2 and r2.imageUri is None


def test_ladder_skips_second_query_when_first_has_candidates():
    client = _FakeClient(default=[_photo(alt="eggs with tomato")], vision=[_vision(matches=False)])
    _run([_recipe()], client)
    assert len(client.searches) == 1


# ── Parseo de dishNameEn / mainIngredientsEn en generate_recipes ──
class _AIResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self._payload)}}]}


class _AIClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.prompts.append(json["messages"][0]["content"])
        return _AIResp(self.payloads.pop(0))


PANTRY = [
    {"id": "p-tom", "name": "Tomates", "quantity": 500, "unit": "grams"},
    {"id": "p-hue", "name": "Huevos", "quantity": 6, "unit": "units"},
]


def _ai_payload(**extra):
    item = {
        "title": "Huevos con tomate", "description": "Huevos revueltos con tomate", "prepTimeMinutes": 15,
        "servings": 2, "difficulty": "easy", "matchScore": 100, "steps": [],
        "availableIngredients": [
            {"name": "Tomate", "inventoryIngredientId": "p-tom", "quantity": 100, "unit": "grams"},
            {"name": "Huevo", "inventoryIngredientId": "p-hue", "quantity": 2, "unit": "units"},
        ],
        "missingIngredients": [],
    }
    item.update(extra)
    return {"recipes": [item]}


@pytest.fixture
def _ai(monkeypatch):
    monkeypatch.setattr(ai_recipe_service.settings, "DEEPSEEK_API_KEY", "test-key", raising=False)

    async def _no_limit(*a, **k):
        return None

    monkeypatch.setattr(ai_recipe_service, "check_rate_limit", _no_limit)


def test_generate_parses_and_sanitizes_english_fields(_ai):
    payload = _ai_payload(
        dishNameEn="Tomato   Scrambled-Eggs!! <b>2</b>",
        mainIngredientsEn=["Egg", " tomato ", "tomato", "", 5, "onion", "garlic", "x" * 100],
    )
    client = _AIClient([payload])
    out = asyncio.run(AIRecipeService.generate_recipes(
        RecipeGenerateRequest(ingredients=PANTRY, count=1), http_client=client))
    assert out[0].dishNameEn == "tomato scrambled-eggs b b"
    assert out[0].mainIngredientsEn == ["egg", "tomato", "onion"]  # máx. 3, sin vacíos ni repetidos
    assert '"dishNameEn"' in client.prompts[0] and '"mainIngredientsEn"' in client.prompts[0]


def test_generate_truncates_long_dish_name(_ai):
    out = asyncio.run(AIRecipeService.generate_recipes(
        RecipeGenerateRequest(ingredients=PANTRY, count=1),
        http_client=_AIClient([_ai_payload(dishNameEn="a" * 200, mainIngredientsEn=["egg"])])))
    assert len(out[0].dishNameEn) == 60


def test_generate_does_not_reject_recipe_without_english_fields(_ai):
    out = asyncio.run(AIRecipeService.generate_recipes(
        RecipeGenerateRequest(ingredients=PANTRY, count=1), http_client=_AIClient([_ai_payload()])))
    assert len(out) == 1 and out[0].dishNameEn is None and out[0].mainIngredientsEn == []
    out = asyncio.run(AIRecipeService.generate_recipes(
        RecipeGenerateRequest(ingredients=PANTRY, count=1),
        http_client=_AIClient([_ai_payload(dishNameEn=123, mainIngredientsEn="egg")])))
    assert out[0].dishNameEn is None and out[0].mainIngredientsEn == []


def test_generate_exclude_titles_clause_only_when_given(_ai):
    client = _AIClient([_ai_payload(), _ai_payload()])
    req = RecipeGenerateRequest(ingredients=PANTRY, count=1)
    asyncio.run(AIRecipeService.generate_recipes(req, http_client=client))
    asyncio.run(AIRecipeService.generate_recipes(
        req, http_client=client, exclude_titles=["Tortilla\nde papa", "Huevos rotos"] + [f"Plato {i}" for i in range(20)]))
    assert "No repitas estos platos" not in client.prompts[0]
    assert "No repitas estos platos: Tortilla de papa; Huevos rotos;" in client.prompts[1]
    assert "Plato 7" in client.prompts[1] and "Plato 8" not in client.prompts[1]  # máx. 10 títulos


# ── Endpoint con PHOTO_REQUIRED ──
def _endpoint_env(monkeypatch, rounds, photo_for):
    """rounds: lista de listas de títulos que devolverá cada llamada a generate_recipes.
    photo_for: función título -> bool (si esa receta recibe foto)."""
    calls = []

    async def fake_generate(req, http_client=None, language="es", exclude_titles=None):
        calls.append({"count": req.count, "exclude": exclude_titles})
        titles = rounds[len(calls) - 1]
        return [_recipe(title=t) for t in titles]

    async def fake_attach(recipes, http_client=None):
        for r in recipes:
            if photo_for(r.title):
                r.imageUri = f"https://img/{r.title}"
        return {"searches": 0}

    async def _no_limit(*a, **k):
        return None

    monkeypatch.setattr(recipes_api.AIRecipeService, "generate_recipes", staticmethod(fake_generate))
    monkeypatch.setattr(recipes_api, "attach_photos", fake_attach)
    monkeypatch.setattr(recipes_api, "check_ai_rate_limit", _no_limit)
    monkeypatch.setattr(recipes_api, "check_rate_limit", _no_limit)
    recipes_api.RECIPE_CACHE.clear()
    return calls


def _call_endpoint(count=2):
    request = SimpleNamespace(headers={}, app=SimpleNamespace(state=SimpleNamespace(http_client=None)))
    req = RecipeGenerateRequest(ingredients=PANTRY, count=count)
    return asyncio.run(recipes_api.generate_recipes(req, request, user_id="u1"))


def test_endpoint_returns_all_recipes_when_photo_not_required(monkeypatch):
    calls = _endpoint_env(monkeypatch, [["A", "B"]], lambda t: t == "A")
    out = _call_endpoint()
    assert [r.title for r in out] == ["A", "B"] and len(calls) == 1


def test_endpoint_photo_required_does_one_extra_round_only(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", True, raising=False)
    calls = _endpoint_env(monkeypatch, [["A", "B"], ["C"]], lambda t: t in ("A", "C"))
    out = _call_endpoint(count=2)
    assert [r.title for r in out] == ["A", "C"]
    assert len(calls) == 2 and calls[1]["count"] == 1 and calls[1]["exclude"] == ["A", "B"]


def test_endpoint_photo_required_never_a_third_round_and_422_when_none(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", True, raising=False)
    calls = _endpoint_env(monkeypatch, [["A", "B"], ["C", "D"], ["E"]], lambda t: False)
    with pytest.raises(HTTPException) as exc:
        _call_endpoint(count=2)
    assert exc.value.status_code == 422
    assert exc.value.detail == "No pudimos armar una receta con foto verificada. Prueba otra selección de ingredientes."
    assert len(calls) == 2


def test_endpoint_extra_round_http_error_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", True, raising=False)
    calls = _endpoint_env(monkeypatch, [["A", "B"]], lambda t: t == "A")

    original = recipes_api.AIRecipeService.generate_recipes

    async def flaky(req, http_client=None, language="es", exclude_titles=None):
        if exclude_titles is not None:
            raise HTTPException(status_code=422, detail="sin combinación")
        return await original(req, http_client=http_client, language=language)

    monkeypatch.setattr(recipes_api.AIRecipeService, "generate_recipes", staticmethod(flaky))
    out = _call_endpoint(count=2)
    assert [r.title for r in out] == ["A"] and len(calls) == 1


def test_endpoint_caches_final_result_with_photos(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", True, raising=False)
    calls = _endpoint_env(monkeypatch, [["A", "B"], ["C"]], lambda t: t in ("A", "C"))
    first = _call_endpoint(count=2)
    second = _call_endpoint(count=2)  # anti-doble-clic: no vuelve a generar
    assert second is first and len(calls) == 2


# ── Revisión: casos adicionales ──
@pytest.mark.parametrize("bad", [
    _Resp(200, {"choices": [{"message": {"content": "esto no es json"}}]}),
    _Resp(200, {"choices": [{"message": {"content": json.dumps({"reason": "sin campos"})}}]}),
    _Resp(200, {"choices": []}),
])
def test_invalid_vision_json_falls_back_to_text_rule(bad):
    client = _FakeClient(default=[_photo(alt="eggs with tomato")], vision=[bad])
    r = _recipe()
    stats = _run([r], client)
    assert r.imageUri is not None and stats["approved_text"] == 1 and stats["vision_calls"] == 1


def test_vision_cap_one_with_three_parallel_recipes_makes_exactly_one_vision_call(monkeypatch):
    monkeypatch.setattr(settings, "VISION_DAILY_CAP", 1, raising=False)
    client = _FakeClient(default=[_photo(alt="eggs with tomato")], vision=[_vision()])
    recipes = [_recipe(dish=f"dish {i}", mains=("egg", "tomato")) for i in range(3)]
    stats = _run(recipes, client)
    assert len(client.vision_payloads) == 1 and stats["vision_calls"] == 1


def test_unsplash_500_no_retry_and_no_block():
    client = _FakeClient(default=[_photo()], vision=[_vision()], search_status=500)
    r = _recipe()
    _run([r], client)
    assert r.imageUri is None and len(client.searches) == 1
    assert not recipe_photo._is_blocked() and recipe_photo.photos_available()


def test_is_food_dish_false_means_no_photo():
    client = _FakeClient(default=[_photo()], vision=[_vision(is_food=False, matches=True)])
    r = _recipe()
    _run([r], client)
    assert r.imageUri is None and client.downloads == []


def test_download_location_with_other_host_is_not_called():
    for evil in ("https://evil.example.com/x", "http://api.unsplash.com/x", "https://api.unsplash.com.evil.com/x",
                 "https://api.unsplash.com@evil.com/x"):
        photo = _photo()
        photo["links"]["download_location"] = evil
        client = _FakeClient(default=[photo], vision=[_vision()])
        recipe_photo._reset_state_for_tests()
        r = _recipe()
        _run([r], client)
        assert r.imageUri is not None and client.downloads == []


def test_text_rule_rejection_is_not_negatively_cached(monkeypatch):
    monkeypatch.setattr(settings, "VISION_DAILY_CAP", 0, raising=False)
    client = _FakeClient(default=[_photo(alt="scrambled eggs on toast")], vision=[_vision()])
    _run([_recipe()], client)
    assert len(recipe_photo._NEGATIVE_CACHE) == 0
    # Al reiniciarse el cupo, la misma receta sí puede verificarse con visión
    monkeypatch.setattr(settings, "VISION_DAILY_CAP", 50, raising=False)
    r = _recipe()
    _run([r], client)
    assert r.imageUri is not None and len(client.vision_payloads) == 1


def test_photos_available_reflects_key_and_block(monkeypatch):
    assert recipe_photo.photos_available()
    recipe_photo._block_unsplash("prueba")
    assert not recipe_photo.photos_available()
    recipe_photo._reset_state_for_tests()
    monkeypatch.setattr(settings, "UNSPLASH_ACCESS_KEY", "", raising=False)
    assert not recipe_photo.photos_available()


def test_endpoint_extra_round_runtime_error_does_not_500(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", True, raising=False)
    calls = _endpoint_env(monkeypatch, [["A", "B"]], lambda t: t == "A")
    original = recipes_api.AIRecipeService.generate_recipes

    async def boom(req, http_client=None, language="es", exclude_titles=None):
        if exclude_titles is not None:
            raise RuntimeError("boom")
        return await original(req, http_client=http_client, language=language)

    monkeypatch.setattr(recipes_api.AIRecipeService, "generate_recipes", staticmethod(boom))
    out = _call_endpoint(count=2)
    assert [r.title for r in out] == ["A"] and len(calls) == 1


def test_endpoint_photo_required_without_unsplash_key_is_422_without_extra_round(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", True, raising=False)
    monkeypatch.setattr(settings, "UNSPLASH_ACCESS_KEY", "", raising=False)
    calls = _endpoint_env(monkeypatch, [["A", "B"], ["C"]], lambda t: False)
    with pytest.raises(HTTPException) as exc:
        _call_endpoint(count=2)
    assert exc.value.status_code == 422 and len(calls) == 1


def test_endpoint_photo_required_with_unsplash_blocked_skips_extra_round(monkeypatch):
    monkeypatch.setattr(settings, "PHOTO_REQUIRED", True, raising=False)
    calls = _endpoint_env(monkeypatch, [["A", "B"], ["C"]], lambda t: t == "A")
    recipe_photo._block_unsplash("prueba")
    out = _call_endpoint(count=2)
    assert [r.title for r in out] == ["A"] and len(calls) == 1
