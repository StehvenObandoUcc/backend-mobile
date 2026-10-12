import logging
import re
from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional
from pydantic import BaseModel, Field, StringConstraints, field_validator

logger = logging.getLogger(__name__)

# Tope de ingredientes que ven a la vez el prompt y el verificador (misma lista para ambos).
MAX_PANTRY_INGREDIENTS = 30
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard", "any"}
_NAME_MAX = 60


def _one_line(value: Any, max_len: int) -> str:
    """Texto en una sola línea (sin saltos ni tabulaciones), recortado."""
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:max_len].strip()


def _clean_expiration(value: Any) -> Optional[str]:
    """'YYYY-MM-DD' real y razonable, o None (una fecha inválida se ignora, nunca rompe la petición)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text if 2024 <= parsed.year <= 2099 else None


def _clean_difficulty(value: Any, default: str) -> str:
    cleaned = str(value or "").strip().lower()
    return cleaned if cleaned in ALLOWED_DIFFICULTIES else default


class RecipeResponse(BaseModel):
    id: str
    title: str = Field(max_length=120)
    description: str = Field(max_length=500)
    prepTimeMinutes: int
    servings: int
    difficulty: str
    matchScore: int
    availableIngredients: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    missingIngredients: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    steps: List[Annotated[str, StringConstraints(max_length=500)]] = Field(default_factory=list, max_length=20)
    isSaved: bool = False
    isPrepared: bool = False
    # Foto real verificada (Unsplash). Opcionales: las apps viejas simplemente los ignoran.
    imageUri: Optional[str] = None
    imageCredit: Optional[Dict[str, str]] = None
    # Términos genéricos en inglés para buscar la foto
    dishNameEn: Optional[str] = None
    mainIngredientsEn: List[str] = Field(default_factory=list)


class RecipeGenerateRequest(BaseModel):
    ingredients: List[Dict[str, Any]] = Field(
        default_factory=list,
        max_length=50,
        description="Lista de ingredientes del inventario del usuario (se usan los primeros 30)",
    )
    max_prep_time: int = Field(
        default=30,
        ge=5,
        le=180,
        description="Tiempo máximo de preparación en minutos",
    )
    focus: str = Field(
        default="waste_reduction",
        max_length=200,
        description="Enfoque: 'waste_reduction' (aprovechar alimentos), 'quick' (rápidas), 'custom' (personalizada) o libre",
    )
    difficulty: Optional[str] = Field(
        default="any",
        max_length=20,
        description="Nivel de dificultad: 'easy', 'medium', 'hard', o 'any'",
    )
    count: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Número de recetas que el usuario solicita generar",
    )
    dietary_preference: Optional[str] = Field(
        default="any",
        max_length=30,
        description="Preferencia dietaria: 'any', 'vegetarian', 'vegan', 'keto', 'gluten_free', 'low_carb'",
    )

    servings: Optional[int] = Field(
        default=None,
        ge=1,
        le=12,
        description="Porciones por receta (Configuración › Porciones por defecto)",
    )
    avoid_ingredients: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="Ingredientes que el usuario no come o le dan alergia (Configuración)",
    )

    @field_validator("avoid_ingredients")
    @classmethod
    def sanitize_avoid(cls, items: List[str]) -> List[str]:
        cleaned = []
        for raw in items[:20]:
            name = re.sub(r'[\r\n\t]+', ' ', str(raw)).strip()[:40]
            if name:
                cleaned.append(name)
        return cleaned

    @field_validator("ingredients")
    @classmethod
    def sanitize_ingredients(cls, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _one_line(item.get("name", ""), _NAME_MAX)
            if not name:
                continue
            cleaned = dict(item)
            cleaned["name"] = name
            # Caducidad opcional: 'YYYY-MM-DD' válida; si no, se ignora (acepta también expiration_date)
            expiration = _clean_expiration(cleaned.pop("expiration_date", None) or cleaned.get("expirationDate"))
            if expiration:
                cleaned["expirationDate"] = expiration
            else:
                cleaned.pop("expirationDate", None)
            sanitized.append(cleaned)
        if len(sanitized) > MAX_PANTRY_INGREDIENTS:
            logger.warning(
                "[RecipeRequest] %d ingredientes recibidos; se recortan a %d (el prompt y la verificación ven la misma lista).",
                len(sanitized), MAX_PANTRY_INGREDIENTS,
            )
            sanitized = sanitized[:MAX_PANTRY_INGREDIENTS]
        return sanitized

    @field_validator("focus", mode="before")
    @classmethod
    def sanitize_focus(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        return _one_line(v, 200) or "waste_reduction"

    @field_validator("difficulty", mode="before")
    @classmethod
    def sanitize_difficulty(cls, v: Any) -> str:
        return _clean_difficulty(v, "any")

    @field_validator("dietary_preference")
    @classmethod
    def sanitize_dietary_preference(cls, v: Optional[str]) -> str:
        if not v:
            return "any"
        cleaned = v.strip().lower()
        allowed = {"any", "vegetarian", "vegan", "keto", "low_carb", "gluten_free"}
        return cleaned if cleaned in allowed else "any"


class RecipeStepsRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120, description="Título de la receta")
    description: Optional[str] = Field(default="", max_length=500, description="Descripción opcional")
    availableIngredients: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    missingIngredients: List[Dict[str, Any]] = Field(default_factory=list, max_length=50)
    difficulty: Optional[str] = Field(default="easy", max_length=20)
    avoid_ingredients: List[str] = Field(default_factory=list, max_length=20)

    @field_validator("avoid_ingredients")
    @classmethod
    def sanitize_avoid(cls, items: List[str]) -> List[str]:
        return [n for n in (re.sub(r"[\r\n\t]+", " ", str(x)).strip()[:40] for x in items[:20]) if n]

    @field_validator("availableIngredients", "missingIngredients")
    @classmethod
    def sanitize_ingredient_names(cls, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Los nombres (van al prompt) en una línea y ≤ 60 caracteres, igual que en la generación."""
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cleaned = dict(item)
            if "name" in cleaned:
                cleaned["name"] = _one_line(cleaned.get("name"), _NAME_MAX)
            out.append(cleaned)
        return out

    @field_validator("difficulty", mode="before")
    @classmethod
    def sanitize_difficulty(cls, v: Any) -> str:
        return _clean_difficulty(v, "easy")


class RecipeStepsResponse(BaseModel):
    steps: List[str] = Field(default_factory=list)



