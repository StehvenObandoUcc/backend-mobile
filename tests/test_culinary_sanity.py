from app.schemas.recipe import RecipeResponse
from app.services.ai_recipe_service import validate_recipe_sanity, normalize_ingredient_name


def test_normalization_removes_accents_and_lowercases():
    assert normalize_ingredient_name("Plátano Maduro") == "platano maduro"
    assert normalize_ingredient_name("Salsa Barbacoa") == "salsa barbacoa"
    assert normalize_ingredient_name("Mayonésa") == "mayonesa"
    assert normalize_ingredient_name("CHÉDAR") == "chedar"


def test_valid_sweet_savory_pairings_are_accepted():
    # 1. Cerdo con manzana y mostaza
    rec1 = RecipeResponse(
        id="rec-1",
        title="Lomo de cerdo con salsa de manzana y mostaza",
        description="Delicioso lomo glaseado con puré de manzana.",
        prepTimeMinutes=25,
        servings=2,
        difficulty="medium",
        matchScore=90,
        availableIngredients=[
            {"id": "1", "name": "Lomo de cerdo", "quantity": 300, "unit": "grams"},
            {"id": "2", "name": "Manzana", "quantity": 2, "unit": "units"},
            {"id": "3", "name": "Mostaza", "quantity": 1, "unit": "units"},
            {"id": "4", "name": "Miel", "quantity": 1, "unit": "units"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid1, reason1 = validate_recipe_sanity(rec1)
    assert is_valid1 is True
    assert reason1 is None

    # 2. Pollo a la naranja con arroz
    rec2 = RecipeResponse(
        id="rec-2",
        title="Pollo a la naranja y cebolla",
        description="Pollo salteado en reducción de naranja.",
        prepTimeMinutes=20,
        servings=2,
        difficulty="easy",
        matchScore=95,
        availableIngredients=[
            {"id": "1", "name": "Pechuga de pollo", "quantity": 250, "unit": "grams"},
            {"id": "2", "name": "Naranja", "quantity": 2, "unit": "units"},
            {"id": "3", "name": "Cebolla", "quantity": 1, "unit": "units"},
            {"id": "4", "name": "Arroz", "quantity": 150, "unit": "grams"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid2, reason2 = validate_recipe_sanity(rec2)
    assert is_valid2 is True
    assert reason2 is None

    # 3. Chocolate con avena, leche y plátano
    rec3 = RecipeResponse(
        id="rec-3",
        title="Bowl de avena con chocolate y plátano",
        description="Desayuno nutritivo y dulce.",
        prepTimeMinutes=10,
        servings=1,
        difficulty="easy",
        matchScore=100,
        availableIngredients=[
            {"id": "1", "name": "Chocolate oscuro", "quantity": 30, "unit": "grams"},
            {"id": "2", "name": "Avena en hojuelas", "quantity": 50, "unit": "grams"},
            {"id": "3", "name": "Leche", "quantity": 200, "unit": "milliliters"},
            {"id": "4", "name": "Plátano", "quantity": 1, "unit": "units"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid3, reason3 = validate_recipe_sanity(rec3)
    assert is_valid3 is True
    assert reason3 is None


def test_obvious_incompatible_pairs_are_rejected():
    # Caso absurdo reportado: Chocolate con salsa BBQ
    rec_absurd = RecipeResponse(
        id="rec-absurd",
        title="Plátano con BBQ y chocolate",
        description="Mezcla experimental.",
        prepTimeMinutes=15,
        servings=1,
        difficulty="easy",
        matchScore=80,
        availableIngredients=[
            {"id": "1", "name": "Plátano maduro", "quantity": 1, "unit": "units"},
            {"id": "2", "name": "Salsa BBQ", "quantity": 2, "unit": "units"},
            {"id": "3", "name": "Chocolate", "quantity": 50, "unit": "grams"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid, reason = validate_recipe_sanity(rec_absurd)
    assert is_valid is False
    assert "incompatible" in reason.lower()

    # Chocolate con mayonesa
    rec_mayo = RecipeResponse(
        id="rec-mayo",
        title="Tostada de chocolate y mayonesa",
        description="Mezcla incoherente.",
        prepTimeMinutes=5,
        servings=1,
        difficulty="easy",
        matchScore=70,
        availableIngredients=[
            {"id": "1", "name": "Chocolate", "quantity": 1, "unit": "units"},
            {"id": "2", "name": "Mayonesa", "quantity": 1, "unit": "units"},
            {"id": "3", "name": "Pan", "quantity": 2, "unit": "units"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid_mayo, reason_mayo = validate_recipe_sanity(rec_mayo)
    assert is_valid_mayo is False
    assert "incompatible" in reason_mayo.lower()


def test_single_ingredient_coherent_is_accepted():
    # Caso 1 solo ingrediente: Huevo -> Omelette francés (plenamente coherente)
    rec_single = RecipeResponse(
        id="rec-single-1",
        title="Omelette francés clásico con hierbas",
        description="Esponjosa tortilla de huevo batido preparada a la perfección.",
        prepTimeMinutes=10,
        servings=1,
        difficulty="easy",
        matchScore=90,
        availableIngredients=[
            {"id": "1", "name": "Huevos", "quantity": 3, "unit": "units"},
        ],
        missingIngredients=[
            {"id": "m1", "name": "Mantequilla", "quantity": 15, "unit": "grams"},
            {"id": "m2", "name": "Sal", "quantity": 1, "unit": "grams"},
        ],
        steps=[],
    )
    is_valid, reason = validate_recipe_sanity(rec_single)
    assert is_valid is True
    assert reason is None


def test_single_ingredient_incoherent_is_rejected():
    # Caso 1 solo ingrediente disponible (Plátano), pero la receta generada es una sopa de pollo que ni lo menciona
    rec_incoherent = RecipeResponse(
        id="rec-single-bad",
        title="Caldo de res con fideos",
        description="Sopa tradicional reconfortante.",
        prepTimeMinutes=25,
        servings=2,
        difficulty="medium",
        matchScore=80,
        availableIngredients=[
            {"id": "1", "name": "Plátano", "quantity": 1, "unit": "units"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid, reason = validate_recipe_sanity(rec_incoherent)
    assert is_valid is False
    assert "no integra coherentemente" in reason.lower()


def test_commercial_beverages_with_savory_are_rejected():
    # Bug 6.2: Caso reportado "Pony Malta" combinada con pollo
    rec_pony_malta = RecipeResponse(
        id="rec-pony-pollo",
        title="Pollo salteado con Pony Malta",
        description="Pechuga de pollo cocinada en reducción de Pony Malta.",
        prepTimeMinutes=20,
        servings=2,
        difficulty="easy",
        matchScore=90,
        availableIngredients=[
            {"id": "1", "name": "Pechuga de pollo", "quantity": 400, "unit": "grams"},
            {"id": "2", "name": "Pony Malta", "quantity": 1, "unit": "units"},
            {"id": "3", "name": "Cebolla", "quantity": 1, "unit": "units"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid, reason = validate_recipe_sanity(rec_pony_malta)
    assert is_valid is False
    assert "incoherente" in reason.lower() or "incompatible" in reason.lower()

    # Gaseosa con pescado
    rec_soda_fish = RecipeResponse(
        id="rec-soda-fish",
        title="Filete de pescado a la gaseosa",
        description="Pescado bañado en gaseosa.",
        prepTimeMinutes=15,
        servings=1,
        difficulty="easy",
        matchScore=85,
        availableIngredients=[
            {"id": "1", "name": "Pescado", "quantity": 200, "unit": "grams"},
            {"id": "2", "name": "Gaseosa", "quantity": 1, "unit": "units"},
        ],
        missingIngredients=[],
        steps=[],
    )
    is_valid_soda, reason_soda = validate_recipe_sanity(rec_soda_fish)
    assert is_valid_soda is False


