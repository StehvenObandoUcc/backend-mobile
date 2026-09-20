from collections import OrderedDict
import logging
import time
from typing import List
from fastapi import APIRouter, Request, status
from app.schemas.recipe import (
    RecipeResponse,
    RecipeGenerateRequest,
    RecipeStepsRequest,
    RecipeStepsResponse,
)
from app.services.ai_recipe_service import AIRecipeService

logger = logging.getLogger(__name__)
router = APIRouter()

# Caché LRU en memoria de un solo proceso (Ponytail Opt 5).
# NOTA ARQUITECTÓNICA: Esta caché es local al proceso actual de FastAPI.
# Se invalida al reiniciar el servidor (ej. con --reload en desarrollo)
# y no es compartida entre múltiples workers si en el futuro se escala horizontalmente.
RECIPE_CACHE: OrderedDict = OrderedDict()
MAX_CACHE_ENTRIES = 128
CACHE_TTL_SECONDS = 86400  # 24 horas

SUGGESTED_RECIPES_MOCK: List[RecipeResponse] = [
    RecipeResponse(
        id="rec-backend-1",
        title="Pollo al Sartén con Tomates y Mozzarella Fundida",
        description="Receta sugerida: combina pechuga dorada con rodajas de tomate y queso fundido encima.",
        prepTimeMinutes=20,
        servings=2,
        difficulty="easy",
        matchScore=95,
        availableIngredients=[
            {"id": "b-1", "name": "Pechuga de pollo", "quantity": 400, "unit": "grams", "isAvailable": True, "isOptional": False, "substitutions": []},
            {"id": "b-2", "name": "Tomate", "quantity": 2, "unit": "units", "isAvailable": True, "isOptional": False, "substitutions": []},
            {"id": "b-3", "name": "Mozzarella fresca", "quantity": 100, "unit": "grams", "isAvailable": True, "isOptional": False, "substitutions": []},
        ],
        missingIngredients=[
            {"id": "b-4", "name": "Aceite de oliva", "quantity": 15, "unit": "milliliters", "isAvailable": False, "isOptional": True, "substitutions": ["Mantequilla"]},
        ],
        steps=[
            "Cortar la pechuga en filetes y sazonar con sal y pimienta.",
            "Dorar el pollo en sartén caliente a fuego medio durante 5 minutos por lado.",
            "Colocar las rodajas de tomate y la mozzarella sobre cada filete.",
            "Tapar la sartén 3 minutos para que el queso se derrita suavemente.",
            "Servir caliente decorado con hierbas frescas.",
        ],
        isSaved=False,
        isPrepared=False,
    ),
    RecipeResponse(
        id="rec-backend-2",
        title="Tortilla Rápida de Huevos y Vegetales Salteados",
        description="Receta rápida: tortilla esponjosa con vegetales picados al dente.",
        prepTimeMinutes=15,
        servings=2,
        difficulty="easy",
        matchScore=88,
        availableIngredients=[
            {"id": "b-5", "name": "Huevos", "quantity": 3, "unit": "units", "isAvailable": True, "isOptional": False, "substitutions": []},
            {"id": "b-6", "name": "Cebolla", "quantity": 1, "unit": "units", "isAvailable": True, "isOptional": False, "substitutions": []},
        ],
        missingIngredients=[
            {"id": "b-7", "name": "Sal y pimienta", "quantity": 1, "unit": "package", "isAvailable": False, "isOptional": True, "substitutions": []},
        ],
        steps=[
            "Batir los huevos con una pizca de sal en un tazón.",
            "Saltear la cebolla picada finamente en sartén durante 3 minutos.",
            "Verter los huevos batidos y cocinar a fuego bajo 4 minutos.",
            "Doblar por la mitad y servir caliente.",
        ],
        isSaved=False,
        isPrepared=False,
    ),
]

@router.get("/recipes", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
async def get_suggested_recipes() -> List[RecipeResponse]:
    """Retorna recetas sugeridas base."""
    return SUGGESTED_RECIPES_MOCK


@router.post("/recipes/generate", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
@router.post("/recipes", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
async def generate_recipes(req: RecipeGenerateRequest, request: Request) -> List[RecipeResponse]:
    """Fase 1: Genera sugerencias de recetas utilizando DeepSeek Chat con caché LRU.
    Clave compuesta obligatoria de 5 dimensiones: ingredientes, dificultad, enfoque, tiempo y cantidad.
    """
    # Construcción estricta de la clave de caché multi-parámetro
    cache_key = (
        tuple(sorted((item.get("name", "").strip().lower() for item in req.ingredients if item.get("name")))),
        req.difficulty or "any",
        req.focus,
        req.max_prep_time,
        req.count,
    )

    now = time.time()
    if cache_key in RECIPE_CACHE:
        entry = RECIPE_CACHE[cache_key]
        if now - entry["timestamp"] < CACHE_TTL_SECONDS:
            RECIPE_CACHE.move_to_end(cache_key)
            logger.info("[Recipes] Cache hit para combinación de recetas (5 ms)")
            return entry["data"]
        else:
            del RECIPE_CACHE[cache_key]

    http_client = getattr(request.app.state, "http_client", None)
    recipes = await AIRecipeService.generate_recipes(req, http_client=http_client)

    # Almacenar en caché LRU
    RECIPE_CACHE[cache_key] = {"data": recipes, "timestamp": now}
    if len(RECIPE_CACHE) > MAX_CACHE_ENTRIES:
        RECIPE_CACHE.popitem(last=False)

    return recipes


@router.post("/recipes/steps", response_model=RecipeStepsResponse, status_code=status.HTTP_200_OK)
async def get_recipe_steps(req: RecipeStepsRequest, request: Request) -> RecipeStepsResponse:
    """Fase 2 (Opción A Stateless): Genera los pasos detallados de preparación bajo demanda."""
    http_client = getattr(request.app.state, "http_client", None)
    steps = await AIRecipeService.generate_recipe_steps(req, http_client=http_client)
    return RecipeStepsResponse(steps=steps)

