from collections import OrderedDict
import logging
import time
from typing import Annotated, List, Optional
from fastapi import APIRouter, Body, Header, HTTPException, Request, status, Depends
from app.core.language import request_language
from app.schemas.recipe import (
    RecipeResponse,
    RecipeGenerateRequest,
    RecipeStepsRequest,
    RecipeStepsResponse,
)
from app.services.ai_recipe_service import AIRecipeService
from app.services.auth_service import AuthService
from app.services.recipe_photo import attach_photos, photos_available
from app.core.config import settings
from app.core.rate_limit import check_rate_limit, check_ai_rate_limit, check_user_daily_recipes, refund_user_daily_recipes
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
    recipe_ids: Annotated[List[str], Body(max_length=200)],
    user_id: str = Depends(get_current_user_id),
):
    """Elimina un lote de recetas guardadas de la base de datos."""
    count = db.delete_saved_recipes_batch(recipe_ids, user_id)
    return {"status": "deleted", "count": count}


def recipe_cache_key(req: RecipeGenerateRequest, user_id: str, language: str) -> tuple:
    """Clave de la caché anti-doble-clic. Debe incluir al usuario y todo lo que cambia el resultado:
    las recetas se verifican contra los ids de SU despensa y respetan SUS ingredientes a evitar."""
    return (
        user_id,
        tuple(sorted(
            (str(item.get("id", "")), item.get("name", "").strip().lower())
            for item in req.ingredients if item.get("name")
        )),
        req.difficulty or "any",
        req.focus,
        req.max_prep_time,
        req.count,
        (req.dietary_preference or "any").lower(),
        req.servings,
        tuple(sorted(a.strip().lower() for a in req.avoid_ingredients)),
        language,
    )


@router.post("/recipes/generate", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
@router.post("/recipes", response_model=List[RecipeResponse], status_code=status.HTTP_200_OK)
async def generate_recipes(
    req: RecipeGenerateRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> List[RecipeResponse]:
    """Fase 1: Genera sugerencias de recetas utilizando DeepSeek Chat con variedad garantizada.
    Caché de corta duración (15s) para evitar bloqueos por doble-tap accidental sin impedir variedad.
    """
    cache_key = recipe_cache_key(req, user_id, request_language(request))

    now = time.time()
    if cache_key in RECIPE_CACHE:
        entry = RECIPE_CACHE[cache_key]
        if now - entry["timestamp"] < CACHE_TTL_SECONDS:
            RECIPE_CACHE.move_to_end(cache_key)
            logger.info("[Recipes] Cache hit para combinación de recetas (anti-doble-clic)")
            return entry["data"]
        else:
            del RECIPE_CACHE[cache_key]

    await check_ai_rate_limit(f"recipe_{user_id}", max_per_minute=settings.MAX_CALLS_PER_MINUTE)
    daily_token = check_user_daily_recipes("generate", user_id)
    try:
        return await _generate_after_quota(req, request, user_id, cache_key, now)
    except Exception as exc:  # noqa: BLE001
        if _should_refund_daily(exc):
            refund_user_daily_recipes("generate", user_id, daily_token)
        raise


def _should_refund_daily(exc: Exception) -> bool:
    """El cupo diario se devuelve si la culpa no fue del usuario: excepción inesperada, 5xx del servidor/IA
    o 429 del límite global (no se llegó a generar nada). Un 422 (la IA sí trabajó) no se devuelve."""
    if isinstance(exc, HTTPException):
        return exc.status_code >= 500 or exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    return True


async def _generate_after_quota(req: RecipeGenerateRequest, request: Request, user_id: str, cache_key: tuple, now: float):
    # Única comprobación del límite global por petición (el servicio y la ronda extra no vuelven a contar)
    await check_rate_limit(settings.MAX_CALLS_PER_MINUTE)
    http_client = getattr(request.app.state, "http_client", None)
    language = request_language(request)
    recipes = await AIRecipeService.generate_recipes(req, http_client=http_client, language=language)

    # Foto real verificada (Unsplash). Nunca hace fallar la entrega mientras PHOTO_REQUIRED sea falso.
    used_photo_ids: set = set()  # misma foto nunca dos veces en una respuesta (ronda extra incluida)
    stats = await attach_photos(recipes, http_client=http_client, used_ids=used_photo_ids, user_id=user_id)
    logger.info(f"[Recipes] Fotos: {stats}")

    if settings.PHOTO_REQUIRED:
        seen_titles = [r.title for r in recipes]
        recipes = [r for r in recipes if r.imageUri]
        missing = req.count - len(recipes)
        if missing > 0 and photos_available():
            # UNA sola ronda extra, sin repetir los platos ya vistos. Nunca más rondas.
            # Sin clave de Unsplash o con Unsplash bloqueado no se hace (no se gasta DeepSeek en vano).
            try:
                extra_req = req.model_copy(update={"count": missing})
                extra = await AIRecipeService.generate_recipes(
                    extra_req, http_client=http_client, language=language, exclude_titles=seen_titles
                )
                extra_stats = await attach_photos(extra, http_client=http_client, used_ids=used_photo_ids, user_id=user_id)
                logger.info(f"[Recipes] Fotos (ronda extra): {extra_stats}")
                recipes.extend(r for r in extra if r.imageUri)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[Recipes] Ronda extra para fotos descartada ({type(exc).__name__}); se sigue con lo que hay.")
        recipes = recipes[:req.count]
        if not recipes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No pudimos armar una receta con foto verificada. Prueba otra selección de ingredientes.",
            )

    # Almacenar en caché temporal (15s)
    RECIPE_CACHE[cache_key] = {"data": recipes, "timestamp": now}
    if len(RECIPE_CACHE) > MAX_CACHE_ENTRIES:
        RECIPE_CACHE.popitem(last=False)

    return recipes


@router.post("/recipes/steps", response_model=RecipeStepsResponse, status_code=status.HTTP_200_OK)
async def get_recipe_steps(
    req: RecipeStepsRequest,
    request: Request,
    user_id: str = Depends(get_current_user_id),
) -> RecipeStepsResponse:
    """Fase 2 (Opción A Stateless): Genera los pasos detallados de preparación bajo demanda."""
    await check_ai_rate_limit(f"steps_{user_id}", max_per_minute=settings.MAX_CALLS_PER_MINUTE)
    daily_token = check_user_daily_recipes("steps", user_id)
    try:
        await check_rate_limit(settings.MAX_CALLS_PER_MINUTE)  # única comprobación global (el servicio no vuelve a contar)
        http_client = getattr(request.app.state, "http_client", None)
        steps = await AIRecipeService.generate_recipe_steps(req, http_client=http_client, language=request_language(request))
    except Exception as exc:  # noqa: BLE001
        if _should_refund_daily(exc):
            refund_user_daily_recipes("steps", user_id, daily_token)
        raise
    return RecipeStepsResponse(steps=steps)
