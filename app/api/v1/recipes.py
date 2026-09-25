from collections import OrderedDict
import logging
import time
from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, Request, status, Depends
from app.schemas.recipe import (
    RecipeResponse,
    RecipeGenerateRequest,
    RecipeStepsRequest,
    RecipeStepsResponse,
)
from app.services.ai_recipe_service import AIRecipeService
from app.services.auth_service import AuthService
from app.core.config import settings
from app.core.rate_limit import check_rate_limit, check_ai_rate_limit, get_client_ip
from app.core import db

logger = logging.getLogger(__name__)

router = APIRouter()

# Caché LRU de corta duración (15 segundos) para protección contra doble-clic/ráfaga accidental.
# Permite generar recetas nuevas y variadas en solicitudes sucesivas (Bug 6.1).
RECIPE_CACHE: OrderedDict = OrderedDict()
MAX_CACHE_ENTRIES = 128
CACHE_TTL_SECONDS = 15

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


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """Extrae el ID del usuario desde el Bearer token JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere autenticación para guardar y sincronizar recetas.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split("Bearer ")[1].strip()
    try:
        user = AuthService.get_current_user_from_token(token)
        return user.id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación inválido o expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.get("/recipes", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
async def get_suggested_recipes(authorization: Optional[str] = Header(None)) -> List[RecipeResponse]:
    """Retorna recetas guardadas si el usuario está autenticado, o sugerencias base por defecto."""
    if authorization and authorization.startswith("Bearer "):
        try:
            token = authorization.split("Bearer ")[1].strip()
            user = AuthService.get_current_user_from_token(token)
            saved = db.get_saved_recipes_by_user(user.id)
            if saved:
                return [RecipeResponse.model_validate(item) for item in saved]
        except Exception:
            pass
    return SUGGESTED_RECIPES_MOCK


@router.get("/recipes/saved", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
async def get_saved_recipes_endpoint(user_id: str = Depends(get_current_user_id)) -> List[RecipeResponse]:
    """Retorna las recetas guardadas del usuario autenticado desde la base de datos (Supabase/SQLite)."""
    items = db.get_saved_recipes_by_user(user_id)
    return [RecipeResponse.model_validate(item) for item in items]


@router.post("/recipes/save", response_model=RecipeResponse, status_code=status.HTTP_201_CREATED)
async def save_recipe_endpoint(
    recipe: RecipeResponse,
    user_id: str = Depends(get_current_user_id),
) -> RecipeResponse:
    """Guarda o actualiza una receta en la base de datos persistente para el usuario."""
    data = recipe.model_dump(by_alias=True)
    saved = db.save_recipe(data, user_id=user_id)
    return RecipeResponse.model_validate(saved)


@router.delete("/recipes/saved/{recipe_id}", status_code=status.HTTP_200_OK)
async def delete_saved_recipe_endpoint(
    recipe_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Elimina físicamente una receta guardada de la base de datos."""
    deleted = db.delete_saved_recipe(recipe_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Receta con ID '{recipe_id}' no encontrada en tus recetas guardadas.",
        )
    return {"status": "deleted", "id": recipe_id, "message": "Receta eliminada de la base de datos con éxito."}


@router.post("/recipes/saved/batch-delete", status_code=status.HTTP_200_OK)
async def batch_delete_recipes_endpoint(
    recipe_ids: List[str],
    user_id: str = Depends(get_current_user_id),
):
    """Elimina un lote de recetas guardadas de la base de datos."""
    count = db.delete_saved_recipes_batch(recipe_ids, user_id)
    return {"status": "deleted", "count": count}


@router.post("/recipes/generate", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
@router.post("/recipes", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
async def generate_recipes(req: RecipeGenerateRequest, request: Request) -> List[RecipeResponse]:
    """Fase 1: Genera sugerencias de recetas utilizando DeepSeek Chat con variedad garantizada.
    Caché de corta duración (15s) para evitar bloqueos por doble-tap accidental sin impedir variedad.
    """
    cache_key = (
        tuple(sorted((item.get("name", "").strip().lower() for item in req.ingredients if item.get("name")))),
        req.difficulty or "any",
        req.focus,
        req.max_prep_time,
        req.count,
        (req.dietary_preference or "any").lower(),
    )

    now = time.time()
    if cache_key in RECIPE_CACHE:
        entry = RECIPE_CACHE[cache_key]
        if now - entry["timestamp"] < CACHE_TTL_SECONDS:
            RECIPE_CACHE.move_to_end(cache_key)
            logger.info("[Recipes] Cache hit para combinación de recetas (anti-doble-clic)")
            return entry["data"]
        else:
            del RECIPE_CACHE[cache_key]

    client_ip = get_client_ip(request)
    await check_ai_rate_limit(f"recipe_{client_ip}", max_per_minute=settings.MAX_CALLS_PER_MINUTE)
    await check_rate_limit(settings.MAX_CALLS_PER_MINUTE)
    http_client = getattr(request.app.state, "http_client", None)
    recipes = await AIRecipeService.generate_recipes(req, http_client=http_client)

    # Almacenar en caché temporal (15s)
    RECIPE_CACHE[cache_key] = {"data": recipes, "timestamp": now}
    if len(RECIPE_CACHE) > MAX_CACHE_ENTRIES:
        RECIPE_CACHE.popitem(last=False)

    return recipes


@router.post("/recipes/steps", response_model=RecipeStepsResponse, status_code=status.HTTP_200_OK)
async def get_recipe_steps(req: RecipeStepsRequest, request: Request) -> RecipeStepsResponse:
    """Fase 2 (Opción A Stateless): Genera los pasos detallados de preparación bajo demanda."""
    client_ip = get_client_ip(request)
    await check_ai_rate_limit(f"steps_{client_ip}", max_per_minute=settings.MAX_CALLS_PER_MINUTE)
    await check_rate_limit(settings.MAX_CALLS_PER_MINUTE)
    http_client = getattr(request.app.state, "http_client", None)
    steps = await AIRecipeService.generate_recipe_steps(req, http_client=http_client)
    return RecipeStepsResponse(steps=steps)
