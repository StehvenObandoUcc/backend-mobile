import pytest
from app.services.ai_recipe_service import sanitize_recipe_ingredient


def test_sanitize_tablespoon():
    ing = {"name": "Aceite de oliva", "quantity": 2, "unit": "tablespoons"}
    res = sanitize_recipe_ingredient(ing)
    assert res["unit"] == "milliliters"
    assert res["quantity"] == 30.0


def test_sanitize_teaspoon():
    ing = {"name": "Ajo en polvo", "quantity": 1, "unit": "teaspoon"}
    res = sanitize_recipe_ingredient(ing)
    assert res["unit"] == "grams"
    assert res["quantity"] == 5.0


def test_sanitize_pinch():
    ing = {"name": "Sal", "quantity": 1, "unit": "pinch"}
    res = sanitize_recipe_ingredient(ing)
    assert res["unit"] == "grams"
    assert res["quantity"] == 1.0


def test_sanitize_cup():
    ing = {"name": "Leche", "quantity": 1.5, "unit": "cups"}
    res = sanitize_recipe_ingredient(ing)
    assert res["unit"] == "milliliters"
    assert res["quantity"] == 360.0


def test_sanitize_unknown_unit():
    ing = {"name": "Alimento raro", "quantity": 3, "unit": "manojo"}
    res = sanitize_recipe_ingredient(ing)
    assert res["unit"] == "units"


def test_sanitize_negative_quantity():
    ing = {"name": "Tomate", "quantity": -1, "unit": "units"}
    res = sanitize_recipe_ingredient(ing)
    assert res["quantity"] == 1
