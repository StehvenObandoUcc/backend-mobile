import json
import logging
import uuid
from typing import List, Optional
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


class AIRecipeService:
    @classmethod
    async def generate_recipes(
        cls,
        req: RecipeGenerateRequest,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> List[RecipeResponse]:
        """Fase 1: Genera resúmenes livianos de recetas (sin pasos) para máxima velocidad (< 2s).
        Los pasos se cargan bajo demanda en la Fase 2 cuando el usuario abre una receta específica.
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
        difficulty_clause = f"- Nivel de dificultad deseado: {req.difficulty}" if req.difficulty and req.difficulty != "any" else "- Dificultad: adaptar a los ingredientes (easy o medium)"

        prompt = f"""
Actúa como chef profesional y nutricionista. Genera EXACTAMENTE {count} recetas DIFERENTES y creativas basadas en los ingredientes del usuario.

Ingredientes disponibles en inventario:
{ingredients_desc or "Ingredientes comunes (huevos, tomate, cebolla, pollo, queso, pasta)"}

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
        payload = {
            "model": "deepseek-chat",
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1500,
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
            logger.error(f"[AIRecipeService] Timeout generando recetas con DeepSeek: {exc}")
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="La IA tardó demasiado en responder al generar recetas. Intenta de nuevo.",
            ) from exc
        except httpx.HTTPStatusError as exc:
            logger.error(f"[AIRecipeService] Error HTTP de DeepSeek {exc.response.status_code}: {exc.response.text[:300]}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="No pudimos generar la receta en este momento. Intenta de nuevo más tarde.",
            ) from exc
        except Exception as exc:
            logger.error(f"[AIRecipeService] Error de conexión con DeepSeek: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Error de conexión con el servicio de IA.",
            ) from exc

        try:
            data = response.json()
            raw_text = data["choices"][0]["message"].get("content") or ""
            logger.info(f"[AIRecipeService] DeepSeek choices[0]: {data.get('choices', [{}])[0]}")
            parsed = json.loads(raw_text.strip())

            # Extraer lista de recetas
            recipe_list = parsed.get("recipes", []) if isinstance(parsed, dict) else parsed
            if not isinstance(recipe_list, list) or len(recipe_list) == 0:
                raise ValueError("DeepSeek devolvió una lista vacía de recetas")

            recipes = []
            for item in recipe_list[:count]:
                recipes.append(
                    RecipeResponse(
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
                )
            return recipes

        except (json.JSONDecodeError, ValidationError, ValueError, KeyError) as exc:
            logger.error(f"[AIRecipeService] Formato JSON inválido de DeepSeek: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="La receta generada no tiene un formato válido. Intenta de nuevo.",
            ) from exc

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
