"""Parte 1 · Reglas culinarias en código (R1–R4, excepciones tradicionales y mínimo de ingredientes). Sin red."""
import asyncio

import pytest

from app.schemas.recipe import RecipeGenerateRequest, RecipeResponse
from app.services import ai_recipe_service
from app.services.ai_recipe_service import COMMERCIAL_BEVERAGES, AIRecipeService
from app.services.culinary_rules import (
    BEVERAGE, NEUTRAL, SAVORY_CONDIMENT, SAVORY_PROTEIN, SWEET_ONLY, VEGETABLE,
    check_culinary, classify_ingredient, has_compatible_pair,
)
from tests.test_recipe_photo import _AIClient


def _rec(avail, missing=(), title="Plato casero", dish=None, profile=None):
    return RecipeResponse(
        id="r1", title=title, description="Un plato clásico", prepTimeMinutes=15, servings=2, difficulty="easy",
        matchScore=90, availableIngredients=[{"name": n} for n in avail], missingIngredients=[{"name": n} for n in missing],
        dishNameEn=dish, flavorProfile=profile,
    )


# ── Clasificación ──
@pytest.mark.parametrize("name,expected", [
    ("Chocolate oscuro", SWEET_ONLY), ("Galletas de vainilla", SWEET_ONLY), ("Cereal de chocolate", SWEET_ONLY),
    ("Leche condensada", SWEET_ONLY), ("Gelatina de fresa", SWEET_ONLY), ("Mermelada de mora", SWEET_ONLY),
    ("Dulce de leche", SWEET_ONLY), ("Azúcar glas", SWEET_ONLY), ("Chispas de chocolate", SWEET_ONLY),
    ("Crema chantilly", SWEET_ONLY), ("Ice cream", SWEET_ONLY), ("Marshmallows", SWEET_ONLY), ("Bocadillo", SWEET_ONLY),
    ("Café molido", BEVERAGE), ("Té verde", BEVERAGE), ("Pony Malta", BEVERAGE), ("Gaseosa", BEVERAGE),
    ("Jugo en caja", BEVERAGE), ("Coffee", BEVERAGE),
    ("Sardinas en lata", SAVORY_PROTEIN), ("Carne de res", SAVORY_PROTEIN), ("Pechuga de pollo", SAVORY_PROTEIN),
    ("Atún", SAVORY_PROTEIN), ("Camarones", SAVORY_PROTEIN), ("Jamón", SAVORY_PROTEIN), ("Pork chops", SAVORY_PROTEIN),
    ("Sardinas en salsa de tomate", SAVORY_PROTEIN),
    ("Mostaza", SAVORY_CONDIMENT), ("Pepinillos", SAVORY_CONDIMENT), ("Salsa de soya", SAVORY_CONDIMENT),
    ("Cebolla larga", SAVORY_CONDIMENT), ("Ajo", SAVORY_CONDIMENT), ("Salsa de tomate", SAVORY_CONDIMENT),
    ("Huevos", NEUTRAL), ("Leche", NEUTRAL), ("Harina", NEUTRAL), ("Plátano maduro", NEUTRAL), ("Queso campesino", NEUTRAL),
    ("Crema de leche", NEUTRAL), ("Limón", NEUTRAL), ("Miel", NEUTRAL), ("Azúcar", NEUTRAL), ("Frutas", NEUTRAL),
    ("Cereal de arroz", NEUTRAL), ("Cereal de avena", NEUTRAL), ("Avena", NEUTRAL), ("Cereal integral", NEUTRAL),
    ("Granola sin azúcar", NEUTRAL), ("Cereales de chocolate", SWEET_ONLY), ("Arroz", NEUTRAL), ("Bicarbonato de soda", NEUTRAL),
])
def test_classify_by_keyword(name, expected):
    assert classify_ingredient(name, None) == expected


def test_classify_false_positives_and_whole_words():
    assert classify_ingredient("Fresas", "fruit") == NEUTRAL  # «res» no está en «fresas»
    assert classify_ingredient("Refresco de piña") == BEVERAGE and classify_ingredient("Ajonjolí") == NEUTRAL
    assert classify_ingredient("Tomate", "vegetable") == VEGETABLE  # «salsa de tomate» ≠ «tomate»
    assert classify_ingredient("Jamón") == SAVORY_PROTEIN and classify_ingredient("Jam") == SWEET_ONLY
    assert classify_ingredient("Leche condensada") == SWEET_ONLY and classify_ingredient("Leche") == NEUTRAL


def test_keyword_wins_over_app_category_and_category_is_the_fallback():
    assert classify_ingredient("Huevos", "protein") == NEUTRAL
    assert classify_ingredient("Cebolla", "vegetable") == SAVORY_CONDIMENT
    assert classify_ingredient("Tofu", "protein") == SAVORY_PROTEIN
    assert classify_ingredient("Lentejas", "legume") == VEGETABLE and classify_ingredient("Zanahoria", "vegetable") == VEGETABLE
    assert classify_ingredient("Pan", "grain") == NEUTRAL and classify_ingredient("Pan", None) == NEUTRAL


def test_commercial_beverages_of_the_sanity_check_are_all_beverages():
    for bev in COMMERCIAL_BEVERAGES:
        assert classify_ingredient(bev) == BEVERAGE, bev


# ── Reglas R1–R4 ──
def test_r3_coffee_with_sardines_is_blocked_with_actionable_reason():
    ok, rule, motivo = check_culinary(_rec(["Sardinas en lata", "Café molido"], title="Tostadas de sardinas al café"))
    assert (ok, rule) == (False, "R3")
    assert "'café molido' (bebida)" in motivo and "'sardinas en lata' (proteína salada)" in motivo
    assert "uno solo como protagonista" in motivo


def test_r1_and_r2_sweet_only_with_savory():
    assert check_culinary(_rec(["Gelatina de fresa", "Sardinas"]))[:2] == (False, "R1")
    assert check_culinary(_rec(["Chocolate"], missing=["Cebolla"]))[:2] == (False, "R2")  # también cuenta lo faltante
    assert check_culinary(_rec(["Mermelada de mora"], missing=["Salsa de soya"]))[:2] == (False, "R2")


def test_r4_profile_against_ingredients():
    ok, rule, motivo = check_culinary(_rec(["Piña", "Cereal de chocolate"], missing=["Leche condensada"], profile="savory"))
    assert (ok, rule) == (False, "R4") and "salada" in motivo
    ok, rule, motivo = check_culinary(_rec(["Plátano", "Salchicha"], profile="sweet"))
    assert (ok, rule) == (False, "R4") and "dulce" in motivo
    assert check_culinary(_rec(["Plátano", "Mostaza"], profile="sweet"))[1] == "R4"
    assert check_culinary(_rec(["Plátano", "Salchicha"], profile=None))[0] is True  # sin perfil no hay R4


def test_realistic_good_recipes_pass():
    assert check_culinary(_rec(["Galletas", "Mermelada de mora"], missing=["Mantequilla"], profile="sweet"))[0]
    assert check_culinary(_rec(["Atún en lata", "Pepinillos"], missing=["Mostaza"], profile="savory"))[0]
    assert check_culinary(_rec(["Salchichas", "Piña", "Limón"], profile="savory"))[0]
    assert check_culinary(_rec(["Plátano maduro", "Queso campesino", "Bocadillo de guayaba"], profile="sweet"))[0]
    assert check_culinary(_rec(["Pollo", "Miel"], missing=["Mostaza"], title="Pollo miel mostaza", profile="savory"))[0]


@pytest.mark.parametrize("title,dish,names,profile", [
    ("Pollo en mole", None, ["Pechuga de pollo", "Chocolate oscuro"], "savory"),
    ("Cerdo mole sencillo", "pork mole", ["Cerdo", "Cacao"], None),
    ("Pizza hawaiana", "hawaiian pizza", ["Jamón", "Mermelada de piña"], "savory"),
    ("Pollo agridulce", "sweet and sour chicken", ["Pollo", "Mermelada de piña"], "sweet"),
    ("Costillas BBQ glaseadas", None, ["Costillas de cerdo", "Mermelada de mora"], "savory"),
    ("Dinner", "bbq ribs", ["Costillas", "Jam"], None),
])
def test_traditional_exceptions_pass(title, dish, names, profile):
    ok, rule, motivo = check_culinary(_rec(names, title=title, dish=dish, profile=profile))
    assert ok, (rule, motivo)


def test_exceptions_are_only_for_their_rule_and_ingredients():
    # Mole excusa R2 (chocolate + ajo/cebolla) pero no R3 (café + pollo)
    assert check_culinary(_rec(["Pollo", "Chocolate"], missing=["Ajo", "Cebolla", "Mostaza"], title="Pollo en mole"))[0]
    assert check_culinary(_rec(["Chocolate"], missing=["Ajo"], title="Postre al chocolate"))[1] == "R2"
    assert check_culinary(_rec(["Pollo", "Café"], title="Pollo en mole"))[1] == "R3"
    # Sin la palabra del plato, la misma mezcla se rechaza
    assert check_culinary(_rec(["Pollo", "Chocolate oscuro"], title="Pollo con chocolate"))[1] == "R1"
    # La excepción de mole no cubre otros dulces
    assert check_culinary(_rec(["Pollo", "Gelatina"], title="Pollo en mole"))[1] == "R1"


# ── has_compatible_pair ──
def _p(*names):
    return [{"id": f"p{i}", "name": n, "category": "other"} for i, n in enumerate(names)]


def test_has_compatible_pair():
    assert has_compatible_pair(_p("Gelatina de fresa", "Sardinas en lata", "Café molido")) is False
    assert has_compatible_pair(_p("Atún en lata", "Mostaza")) is True
    assert has_compatible_pair(_p("Galletas", "Mermelada")) is True
    assert has_compatible_pair(_p("Café", "Leche")) is True
    assert has_compatible_pair(_p("Sardinas")) is False and has_compatible_pair([]) is False
    assert has_compatible_pair(_p("Leche condensada", "Salchichas", "Mayonesa")) is True  # salchicha + mayonesa


# ── Integración con la generación ──
def _payload(title, avail, profile="savory", missing=()):
    return {"recipes": [{
        "title": title, "description": f"{title} casero", "prepTimeMinutes": 15, "servings": 2, "difficulty": "easy",
        "matchScore": 90, "flavorProfile": profile, "steps": [],
        "availableIngredients": [
            {"name": n, "inventoryIngredientId": i, "quantity": 1, "unit": "units"} for n, i in avail
        ],
        "missingIngredients": [{"name": n, "quantity": 1, "unit": "units", "isOptional": True} for n in missing],
    }]}


@pytest.fixture
def _ai(monkeypatch):
    monkeypatch.setattr(ai_recipe_service.settings, "DEEPSEEK_API_KEY", "test-key", raising=False)


def test_generation_retries_with_reason_when_ai_violates_r3(_ai):
    pantry = [
        {"id": "s1", "name": "Sardinas en lata", "quantity": 2, "unit": "units"},
        {"id": "c1", "name": "Café molido", "quantity": 100, "unit": "grams"},
        {"id": "t1", "name": "Tomates", "quantity": 4, "unit": "units"},
    ]
    client = _AIClient([
        _payload("Tostadas de sardinas al café", [("Sardinas", "s1"), ("Café molido", "c1")]),
        _payload("Sardinas con tomate", [("Sardinas", "s1"), ("Tomates", "t1")]),
    ])
    out = asyncio.run(AIRecipeService.generate_recipes(RecipeGenerateRequest(ingredients=pantry, count=1), http_client=client))
    assert [r.title for r in out] == ["Sardinas con tomate"] and out[0].flavorProfile == "savory"
    assert len(client.prompts) == 2
    assert "NOTA DE CORRECCIÓN" in client.prompts[1] and "'café molido' (bebida)" in client.prompts[1]


def test_generation_persistent_violation_is_discarded_with_422(_ai):
    from fastapi import HTTPException
    pantry = [{"id": "s1", "name": "Sardinas", "quantity": 2, "unit": "units"}, {"id": "c1", "name": "Café", "quantity": 1, "unit": "units"},
              {"id": "t1", "name": "Tomates", "quantity": 4, "unit": "units"}]
    bad = _payload("Sardinas al café", [("Sardinas", "s1"), ("Café", "c1")])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(AIRecipeService.generate_recipes(RecipeGenerateRequest(ingredients=pantry, count=1), http_client=_AIClient([bad, bad])))
    assert exc.value.status_code == 422


def test_incoherent_pantry_accepts_a_single_ingredient_and_prompt_says_so(_ai):
    pantry = [
        {"id": "g1", "name": "Gelatina de fresa", "quantity": 1, "unit": "units"},
        {"id": "s1", "name": "Sardinas en lata", "quantity": 2, "unit": "units"},
        {"id": "c1", "name": "Café molido", "quantity": 100, "unit": "grams"},
    ]
    client = _AIClient([_payload("Tostadas de sardinas", [("Sardinas", "s1")], missing=["Pan", "Aceite"])])
    out = asyncio.run(AIRecipeService.generate_recipes(RecipeGenerateRequest(ingredients=pantry, count=1), http_client=client))
    assert len(out) == 1 and len(client.prompts) == 1  # sin reintento: 1 ingrediente basta
    prompt = client.prompts[0]
    assert "entre 1 y 6 ingredientes" in prompt and "usa UNO solo como protagonista" in prompt and "nunca fuerces una mezcla" in prompt
    assert '"flavorProfile": "savory"' in prompt and "cumple un papel claro" in prompt and "Nombre de recetario" in prompt


def test_compatible_pantry_keeps_min_two(_ai):
    pantry = [{"id": "a1", "name": "Atún en lata", "quantity": 2, "unit": "units"}, {"id": "m1", "name": "Mostaza", "quantity": 1, "unit": "units"}]
    client = _AIClient([_payload("Atún", [("Atún en lata", "a1")]), _payload("Atún con mostaza", [("Atún en lata", "a1"), ("Mostaza", "m1")])])
    out = asyncio.run(AIRecipeService.generate_recipes(RecipeGenerateRequest(ingredients=pantry, count=1), http_client=client))
    assert "entre 2 y 6 ingredientes" in client.prompts[0] and len(client.prompts) == 2  # 1 solo ingrediente: reintento
    assert out[0].title == "Atún con mostaza"


def test_flavor_profile_invalid_is_none_not_error():
    for raw, expected in [("sweet", "sweet"), (" SAVORY ", "savory"), ("salado", None), (5, None), (None, None), ("", None)]:
        r = RecipeResponse(id="r", title="Plato", description="d", prepTimeMinutes=5, servings=1, difficulty="easy", matchScore=1, flavorProfile=raw)
        assert r.flavorProfile == expected


def _mole_pantry():
    return [
        {"id": "c1", "name": "Chocolate oscuro", "quantity": 100, "unit": "grams"},
        {"id": "p1", "name": "Pollo", "quantity": 500, "unit": "grams"},
        {"id": "a1", "name": "Ajo", "quantity": 4, "unit": "units"},
        {"id": "o1", "name": "Cebolla", "quantity": 2, "unit": "units"},
    ]


def _mole_payload(title, dish=None):
    item = _payload(title, [("Chocolate oscuro", "c1"), ("Pollo", "p1"), ("Ajo", "a1"), ("Cebolla", "o1")])
    if dish:
        item["recipes"][0]["dishNameEn"] = dish
    item["recipes"][0]["description"] = f"{title} con chocolate, pollo, ajo y cebolla"
    return item


def test_mole_with_chocolate_chicken_garlic_onion_passes_whole_flow(_ai):
    from app.services.ai_recipe_service import validate_recipe_sanity
    client = _AIClient([_mole_payload("Mole poblano")])
    out = asyncio.run(AIRecipeService.generate_recipes(
        RecipeGenerateRequest(ingredients=_mole_pantry(), count=1), http_client=client))
    assert [r.title for r in out] == ["Mole poblano"] and len(client.prompts) == 1  # sin reintento
    assert validate_recipe_sanity(out[0])[0] and check_culinary(out[0], _mole_pantry())[0]
    # También por dishNameEn
    client = _AIClient([_mole_payload("Pollo especial", dish="chicken mole")])
    out = asyncio.run(AIRecipeService.generate_recipes(
        RecipeGenerateRequest(ingredients=_mole_pantry(), count=1), http_client=client))
    assert len(client.prompts) == 1 and out[0].dishNameEn == "chicken mole"


def test_same_combination_without_mole_is_rejected(_ai):
    from fastapi import HTTPException
    bad = _mole_payload("Pollo al chocolate con cebolla")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(AIRecipeService.generate_recipes(
            RecipeGenerateRequest(ingredients=_mole_pantry(), count=1), http_client=_AIClient([bad, bad])))
    assert exc.value.status_code == 422


def test_exception_excuses_an_item_only_if_the_defining_pair_is_present():
    # Mole dulce con pollo pero sin chocolate/cacao: el pollo no se excusa -> R4
    assert check_culinary(_rec(["Pollo", "Cebolla"], title="Pollo en mole", profile="sweet"))[1] == "R4"
    # Mole salado con chocolate pero sin proteína: el chocolate no se excusa -> R4
    assert check_culinary(_rec(["Chocolate"], title="Mole sencillo", profile="savory"))[1] == "R4"
    # Hawaiana salada con mermelada y sin cerdo/pollo/jamón -> rechaza; con jamón pasa
    assert check_culinary(_rec(["Mermelada de piña", "Piña"], title="Pizza hawaiana", profile="savory"))[1] == "R4"
    assert check_culinary(_rec(["Mermelada de piña", "Jamón"], title="Pizza hawaiana", profile="savory"))[0]
    # BBQ con mermelada sin cerdo/res -> rechaza
    assert check_culinary(_rec(["Mermelada de mora"], title="Salsa BBQ", dish="costillas bbq", profile="savory"))[1] == "R4"
