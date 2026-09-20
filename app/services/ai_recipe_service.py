import json
import logging
import unicodedata
import uuid
from typing import List, Optional, Tuple
import httpx
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.core.config import settings
from app.core.rate_limit import check_rate_limit
from app.schemas.recipe import (
    RecipeResponse,
    RecipeGenerateRequest,
    RecipeStepsRequest,
)

logger = logging.getLogger(__name__)


def normalize_ingredient_name(value: str) -> str:
    """Normaliza nombres de ingredientes removiendo tildes, signos diacríticos y mayúsculas."""
    value = value.lower().strip()
    value = unicodedata.normalize("NFD", value)
    return "".join(char for char in value if unicodedata.category(char) != "Mn")


INCOMPATIBLE_PAIRS = [
    {"chocolate", "bbq"},
    {"chocolate", "barbacoa"},
    {"chocolate", "mayonesa"},
    {"chocolate", "ketchup"},
]


def validate_recipe_sanity(recipe: RecipeResponse) -> Tuple[bool, Optional[str]]:
    """Valida que la receta cumpla criterios mínimos de estructura y coherencia gastronómica."""
    title = recipe.title.strip()
    if not title or len(title) < 3:
        return False, "Título de receta vacío o inválido"

    if not recipe.availableIngredients:
        return False, "La receta no contiene ingredientes disponibles"

    norm_names = [
        normalize_ingredient_name(str(ing.get("name", "")))
        for ing in recipe.availableIngredients
        if isinstance(ing, dict) and ing.get("name")
    ]

    for pair in INCOMPATIBLE_PAIRS:
        if all(any(term in name for name in norm_names) for term in pair):
            return False, f"Combinación incompatible detectada en ingredientes: {pair}"

    return True, None


class AIRecipeService:
    @classmethod
    async def generate_recipes(
        cls,
        req: RecipeGenerateRequest,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> List[RecipeResponse]:
        """Fase 1: Genera resúmenes livianos de recetas (sin pasos) para máxima velocidad (< 2s).
        Los pasos se cargan bajo demanda en la Fase 2 cuando el usuario abre una receta específica.
        Incluye validación de coherencia culinaria y reintento correctivo único ante inconsistencias.
        """
        await check_rate_limit(settings.MAX_CALLS_PER_MINUTE)

        ingredients_desc = ", ".join(
            [
                f"{item.get('name')} ({item.get('quantity', '')} {item.get('unit', '')})".strip()
                for item in req.ingredients
                if item.get("name")
            ]
        )
        count = max(1, min(req.count, 3))
        difficulty_clause = (
            f"- Nivel de dificultad deseado: {req.difficulty}"
            if req.difficulty and req.difficulty != "any"
            else "- Dificultad: adaptar a los ingredientes (easy o medium)"
        )

        base_prompt = f"""
Actúa como chef profesional y nutricionista. Genera EXACTAMENTE {count} recetas DIFERENTES basadas en los ingredientes del usuario.

Ingredientes disponibles en inventario:
{ingredients_desc or "Ingredientes comunes (huevos, tomate, cebolla, pollo, queso, pasta)"}

Criterios de Plausibilidad y Armonía Culinaria:
1. Sentido Gastronómico Real: Cada receta debe ser plausible, apetitosa y pertenecer a una tradición o práctica culinaria reconocible.
2. Combinaciones Dulce-Salado: No mezcles ingredientes dulces y salados automáticamente solo porque están disponibles en la despensa. Una combinación dulce-salada solo es válida cuando:
   - Existe un plato reconocible que la justifique (ej. cerdo con manzana y mostaza, pollo a la naranja, costillas con glaseado agridulce, pato a la ciruela, plátano macho con queso/frijoles, chocolate con chile).
   - Hay un ingrediente principal claro que lidera el perfil de sabor.
   - La salsa o técnica de cocción conecta armónicamente los sabores.
   - El resultado sería apetitoso para un comensal común.
   Si no existe una conexión culinaria clara, separa los ingredientes o usa solo los que combinen bien.
3. Subconjunto Coherente: Prioriza una receta principal coherente con un subconjunto armónico de ingredientes. Si algunos ingredientes no encajan (ej. chocolate con salsa barbacoa), NO los fuerces en el mismo plato; déjalos fuera.
4. Ingrediente Principal: Define claramente la proteína, vegetal o base del plato.

Restricciones:
- Cantidad: EXACTAMENTE {count} receta(s).
- Tiempo máximo de preparación: {req.max_prep_time} minutos.
- Enfoque culinario: {req.focus}.
{difficulty_clause}
- matchScore: Porcentaje entero entre 60 y 100 según ingredientes disponibles.
- IMPORTANTE: No generes pasos de preparación en esta fase para optimizar la velocidad de respuesta. Deja el arreglo "steps" vacío [].

Devuelve EXCLUSIVAMENTE un objeto JSON válido con la clave "recipes":
{{
  "recipes": [
    {{
      "title": "Nombre de la receta",
      "description": "Descripción apetitosa de 1 oración.",
      "prepTimeMinutes": {min(req.max_prep_time, 25)},
      "servings": 2,
      "difficulty": "easy",
      "matchScore": 95,
      "availableIngredients": [
        {{"id": "av-1", "name": "Nombre ingrediente", "quantity": 1, "unit": "units", "isAvailable": true, "isOptional": false, "substitutions": []}}
      ],
      "missingIngredients": [
        {{"id": "ms-1", "name": "Aceite de oliva o sal", "quantity": 1, "unit": "package", "isAvailable": false, "isOptional": true, "substitutions": ["Mantequilla"]}}
      ],
      "steps": []
    }}
  ]
}}
"""
        url = f"{settings.DEEPSEEK_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }

        async def _call_deepseek(prompt_text: str) -> List[RecipeResponse]:
            payload = {
                "model": "deepseek-chat",
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": prompt_text}],
                "temperature": 0.4,
                "max_tokens": 1500,
            }

            if http_client is not None:
                response = await http_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=settings.DEEPSEEK_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            else:
                async with httpx.AsyncClient() as fallback_client:
                    response = await fallback_client.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=settings.DEEPSEEK_TIMEOUT_SECONDS,
                    )
                    response.raise_for_status()

            data = response.json()
            raw_text = data["choices"][0]["message"].get("content") or ""
            if not raw_text.strip():
                raise ValueError("DeepSeek devolvió contenido vacío")

            parsed = json.loads(raw_text.strip())
            recipe_list = parsed.get("recipes", []) if isinstance(parsed, dict) else parsed
            if not isinstance(recipe_list, list) or len(recipe_list) == 0:
                raise ValueError("DeepSeek devolvió una lista vacía de recetas")

            parsed_recipes: List[RecipeResponse] = []
            for item in recipe_list[:count]:
                rec = RecipeResponse(
                    id=f"rec-ai-{uuid.uuid4().hex[:6]}",
                    title=str(item.get("title", "Receta sugerida")).strip(),
                    description=str(item.get("description", "")).strip(),
                    prepTimeMinutes=int(item.get("prepTimeMinutes", req.max_prep_time)),
                    servings=int(item.get("servings", 2)),
                    difficulty=item.get("difficulty", "easy"),
                    matchScore=int(item.get("matchScore", 90)),
                    availableIngredients=item.get("availableIngredients", []),
                    missingIngredients=item.get("missingIngredients", []),
                    steps=[],
                    isSaved=False,
                    isPrepared=False,
                )
                parsed_recipes.append(rec)

            # Validación de coherencia y plausibilidad en cada receta
            for r in parsed_recipes:
                is_valid, reason = validate_recipe_sanity(r)
                if not is_valid:
                    raise ValueError(reason or "Fallo de coherencia gastronómica")

            return parsed_recipes

        # Intento 1
        try:
            return await _call_deepseek(base_prompt)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as net_err:
            # Errores de red o de autenticación no se reintentan
            if isinstance(net_err, httpx.HTTPStatusError) and net_err.response.status_code in [401, 403]:
                logger.error(f"[AIRecipeService] Error de credenciales DeepSeek: {net_err}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Error de autenticación con el proveedor de IA.",
                ) from net_err
            logger.error(f"[AIRecipeService] Error de conexión DeepSeek: {net_err}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No pudimos generar la receta en este momento. Intenta de nuevo más tarde.",
            ) from net_err
        except Exception as first_attempt_err:
            logger.warning(
                f"[AIRecipeService] Primer intento falló ({first_attempt_err}). Ejecutando reintento correctivo único..."
            )
            # Reintento 2: Inyectar nota de corrección técnica resumida
            corrective_prompt = (
                f"{base_prompt}\n\n"
                f"NOTA DE CORRECCIÓN: La sugerencia anterior no fue válida ({first_attempt_err}). "
                "Asegúrate de generar un objeto JSON válido con recetas plenamente apetitosas, lógicas y "
                "utilizando exclusivamente un subconjunto coherente de los ingredientes sin forzar combinaciones incompatibles."
            )
            try:
                return await _call_deepseek(corrective_prompt)
            except Exception as retry_err:
                logger.error(f"[AIRecipeService] Reintento fallido: {retry_err}")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="No encontramos una combinación clara con esos ingredientes. Prueba seleccionando alimentos de una misma preparación.",
                ) from retry_err

    @classmethod
    async def generate_recipe_steps(
        cls,
        req: RecipeStepsRequest,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> List[str]:
        """Fase 2: Genera los pasos detallados de preparación exclusivamente cuando el usuario abre la receta."""
        await check_rate_limit(settings.MAX_CALLS_PER_MINUTE)

        available_desc = ", ".join([ing.get("name", "") for ing in req.availableIngredients if ing.get("name")])
        missing_desc = ", ".join([ing.get("name", "") for ing in req.missingIngredients if ing.get("name")])

        prompt = f"""
Actúa como chef profesional. Genera las instrucciones de preparación paso a paso para esta receta:
Título: {req.title}
Descripción: {req.description}
Dificultad: {req.difficulty}
Ingredientes principales: {available_desc or 'Ingredientes estándar'}
Ingredientes complementarios: {missing_desc or 'Ninguno'}

Devuelve EXCLUSIVAMENTE un objeto JSON válido con la clave "steps" conteniendo un arreglo de 4 a 6 pasos claros y concisos:
{{
  "steps": [
    "Paso 1: Instrucción detallada...",
    "Paso 2: Instrucción detallada...",
    "Paso 3: Instrucción detallada...",
    "Paso 4: Instrucción detallada..."
  ]
}}
"""
        url = f"{settings.DEEPSEEK_BASE_URL}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "deepseek-chat",
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            "max_tokens": 1024,
        }

        try:
            if http_client is not None:
                response = await http_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=settings.DEEPSEEK_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            else:
                async with httpx.AsyncClient() as fallback_client:
                    response = await fallback_client.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=settings.DEEPSEEK_TIMEOUT_SECONDS,
                    )
                    response.raise_for_status()
        except httpx.TimeoutException as exc:
            logger.error(f"[AIRecipeService] Timeout generando pasos de receta: {exc}")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="La IA tardó demasiado en responder al generar los pasos. Intenta de nuevo.",
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.error(f"[AIRecipeService] Error HTTP al generar pasos {exc.response.status_code}: {exc.response.text[:300]}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No pudimos generar los pasos en este momento. Intenta de nuevo.",
            ) from exc
        except Exception as exc:
            logger.error(f"[AIRecipeService] Error de conexión al generar pasos: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Error de conexión con el servicio de IA.",
            ) from exc

        try:
            data = response.json()
            raw_text = data["choices"][0]["message"].get("content") or ""
            parsed = json.loads(raw_text.strip())
            steps = parsed.get("steps", [])
            if not isinstance(steps, list) or len(steps) == 0:
                raise ValueError("No se encontraron pasos en la respuesta de la IA")
            return [str(s).strip() for s in steps if str(s).strip()]
        except Exception as exc:
            logger.error(f"[AIRecipeService] Formato inválido en pasos: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No pudimos procesar los pasos de la receta. Intenta de nuevo.",
            ) from exc
