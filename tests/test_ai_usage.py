"""Parte 4 · Contador de uso de DeepSeek y script de evaluación (sin red, sin tocar el JSON real del script)."""
import asyncio
import importlib.util
import json
import os

import httpx
import pytest

from app.schemas.recipe import RecipeGenerateRequest
from app.services import ai_recipe_service, ai_usage, recipe_photo
from app.services.ai_recipe_service import AIRecipeService
from tests.test_recipe_photo import _FakeClient, _photo, _recipe, _vision


@pytest.fixture(autouse=True)
def _clean():
    ai_usage.reset()
    yield
    ai_usage.reset()


def test_record_snapshot_reset():
    snap = ai_usage.snapshot()
    assert snap["total"] == {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
    assert set(snap) == {"recipes", "steps", "vision", "total"}
    ai_usage.record("recipes", {"usage": {"prompt_tokens": 100, "completion_tokens": 40}})
    ai_usage.record("recipes", {"usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    ai_usage.record("vision", {"usage": {"prompt_tokens": 7, "completion_tokens": 1}})
    snap = ai_usage.snapshot()
    assert snap["recipes"] == {"calls": 2, "prompt_tokens": 110, "completion_tokens": 45}
    assert snap["vision"]["calls"] == 1 and snap["steps"]["calls"] == 0
    assert snap["total"] == {"calls": 3, "prompt_tokens": 117, "completion_tokens": 46}
    snap["recipes"]["calls"] = 99  # es una copia
    assert ai_usage.snapshot()["recipes"]["calls"] == 2
    ai_usage.reset()
    assert ai_usage.snapshot()["total"]["calls"] == 0


@pytest.mark.parametrize("bad", [None, {}, {"usage": None}, {"usage": "x"}, [], "texto", 5, {"choices": []}])
def test_record_without_usage_has_no_effect_and_never_raises(bad):
    ai_usage.record("recipes", bad)
    assert ai_usage.snapshot()["total"]["calls"] == 0


def test_record_ignores_non_numeric_token_counts():
    ai_usage.record("steps", {"usage": {"prompt_tokens": "9", "completion_tokens": True}})
    assert ai_usage.snapshot()["steps"] == {"calls": 1, "prompt_tokens": 0, "completion_tokens": 0}


class _UsageResp:
    def __init__(self, payload, usage):
        self._payload, self._usage = payload, usage

    def raise_for_status(self):
        return None

    def json(self):
        body = {"choices": [{"message": {"content": json.dumps(self._payload)}}]}
        if self._usage:
            body["usage"] = self._usage
        return body


class _UsageClient:
    def __init__(self, items):
        self.items = list(items)  # [(payload, usage)]

    async def post(self, url, headers=None, json=None, timeout=None):
        return _UsageResp(*self.items.pop(0))


def _recipes_payload():
    return {"recipes": [{
        "title": "Huevos con tomate", "description": "Huevos revueltos con tomate", "prepTimeMinutes": 15, "servings": 2,
        "difficulty": "easy", "matchScore": 100, "steps": [], "flavorProfile": "savory",
        "availableIngredients": [
            {"name": "Tomate", "inventoryIngredientId": "p-tom", "quantity": 100, "unit": "grams"},
            {"name": "Huevo", "inventoryIngredientId": "p-hue", "quantity": 2, "unit": "units"},
        ],
        "missingIngredients": [],
    }]}


def test_recipes_and_steps_responses_are_counted(monkeypatch):
    monkeypatch.setattr(ai_recipe_service.settings, "DEEPSEEK_API_KEY", "k", raising=False)
    pantry = [{"id": "p-tom", "name": "Tomates", "quantity": 500, "unit": "grams"},
              {"id": "p-hue", "name": "Huevos", "quantity": 6, "unit": "units"}]
    client = _UsageClient([(_recipes_payload(), {"prompt_tokens": 900, "completion_tokens": 300})])
    asyncio.run(AIRecipeService.generate_recipes(RecipeGenerateRequest(ingredients=pantry, count=1), http_client=client))
    assert ai_usage.snapshot()["recipes"] == {"calls": 1, "prompt_tokens": 900, "completion_tokens": 300}
    # Sin `usage` en la respuesta no se cuenta nada
    client = _UsageClient([(_recipes_payload(), None)])
    asyncio.run(AIRecipeService.generate_recipes(RecipeGenerateRequest(ingredients=pantry, count=1), http_client=client))
    assert ai_usage.snapshot()["recipes"]["calls"] == 1

    steps = _UsageClient([({"steps": ["Paso 1: bate los huevos con el tomate."]}, {"prompt_tokens": 50, "completion_tokens": 20})])
    asyncio.run(AIRecipeService._request_steps("prompt", steps))
    assert ai_usage.snapshot()["steps"] == {"calls": 1, "prompt_tokens": 50, "completion_tokens": 20}


def test_vision_response_is_counted(monkeypatch):
    monkeypatch.setattr(recipe_photo, "get_dish_photo", lambda *a, **k: None)
    monkeypatch.setattr(recipe_photo, "save_dish_photo", lambda *a, **k: True)
    from app.core.config import settings
    for name, value in (("UNSPLASH_ACCESS_KEY", "u"), ("DEEPSEEK_API_KEY", "d"), ("VISION_DAILY_CAP", 50)):
        monkeypatch.setattr(settings, name, value, raising=False)
    recipe_photo._reset_state_for_tests()
    resp = _vision()
    resp._data["usage"] = {"prompt_tokens": 1200, "completion_tokens": 30}
    client = _FakeClient(default=[_photo()], vision=[resp])
    asyncio.run(recipe_photo.attach_photos([_recipe()], http_client=client))
    recipe_photo._reset_state_for_tests()
    assert ai_usage.snapshot()["vision"] == {"calls": 1, "prompt_tokens": 1200, "completion_tokens": 30}


# ── scripts/eval_recipes.py ──
def _load_script(tmp_path):
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "eval_recipes.py")
    spec = importlib.util.spec_from_file_location("eval_recipes_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.__file__ = str(tmp_path / "eval_recipes.py")  # el JSON de resultados se escribe en tmp, no en scripts/
    return mod


def test_script_has_new_scenarios_after_the_incoherent_ones_and_shortcut(tmp_path):
    mod = _load_script(tmp_path)
    names = [s[0] for s in mod.SCENARIOS]
    assert names[-5:] == mod.INCOHERENTES + mod.EXCEPCIONES
    assert mod.INCOHERENTES == ["incoherente-mezcla", "incoherente-extremo", "incoherente-snacks"]
    assert mod.EXCEPCIONES == ["excepciones-tradicionales", "maduro-queso"]
    by_name = {s[0]: s for s in mod.SCENARIOS}
    assert [i["name"] for i in by_name["excepciones-tradicionales"][1]] == [
        "Pollo", "Piña", "Pimentón", "Cebolla", "Salsa de soya", "Chocolate oscuro"]
    assert [i["name"] for i in by_name["maduro-queso"][1]] == ["Plátano maduro", "Queso campesino", "Bocadillo de guayaba"]
    assert "--excepciones" in mod.__doc__ and "tokens_por_receta_aceptada" in mod.__doc__


def test_script_reports_tokens_retries_and_rejections_by_rule(monkeypatch, tmp_path, capsys):
    from app.core.config import settings
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "k", raising=False)
    mod = _load_script(tmp_path)
    bad = {"recipes": [{
        "title": "Tostadas de sardinas al café", "description": "Tostadas con sardinas y café", "prepTimeMinutes": 10,
        "servings": 2, "difficulty": "easy", "matchScore": 90, "flavorProfile": "savory", "steps": [],
        "availableIngredients": [
            {"name": "Sardinas en lata", "inventoryIngredientId": "t2", "quantity": 1, "unit": "units"},
            {"name": "Café molido", "inventoryIngredientId": "t3", "quantity": 10, "unit": "grams"}],
        "missingIngredients": []}]}
    good = {"recipes": [dict(bad["recipes"][0], title="Tostadas de sardinas", description="Sardinas sobre pan",
                             availableIngredients=[bad["recipes"][0]["availableIngredients"][0]],
                             missingIngredients=[{"name": "Pan", "quantity": 2, "unit": "units", "isOptional": False}])]}
    client = _UsageClient([(bad, {"prompt_tokens": 100, "completion_tokens": 50}), (good, {"prompt_tokens": 120, "completion_tokens": 60})])

    class _FakeAsyncClient:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    selected = [s for s in mod.SCENARIOS if s[0] == "incoherente-extremo"]
    assert asyncio.run(mod.main(selected)) == 0
    out = capsys.readouterr().out
    summary = json.loads((tmp_path / "eval_recipes_result.json").read_text(encoding="utf-8"))
    assert summary["rechazos_por_regla"] == {"R1": 0, "R2": 0, "R3": 1, "R4": 0, "verificador": 0, "coherencia": 0}
    assert summary["reintentos"] == 1 and summary["recetas_entregadas"] == 1
    assert summary["tokens_de_entrada"] == 220 and summary["tokens_de_salida"] == 110
    assert summary["tokens_por_receta_aceptada"] == 330
    assert summary["uso_deepseek"]["recipes"]["calls"] == 2
    assert summary["recetas_completas"][0]["flavorProfile"] == "savory"
    assert "perfil=savory" in out
    # Los parches del script se deshacen
    assert ai_recipe_service.check_culinary.__module__ == "app.services.culinary_rules"
