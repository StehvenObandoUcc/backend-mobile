import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

class RecipeResponse(BaseModel):
    id: str
    title: str
    description: str
    prepTimeMinutes: int
    servings: int
    difficulty: str
    matchScore: int
    availableIngredients: List[Dict[str, Any]] = Field(default_factory=list)
    missingIngredients: List[Dict[str, Any]] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    isSaved: bool = False
    isPrepared: bool = False


class RecipeGenerateRequest(BaseModel):
    ingredients: List[Dict[str, Any]] = Field(
        default_factory=list,
        max_length=50,
        description="Lista de ingredientes del inventario del usuario (máximo 50)",
    )
    max_prep_time: int = Field(
        default=30,
        ge=5,
        le=180,
        description="Tiempo máximo de preparación en minutos",
    )
    focus: str = Field(
        default="waste_reduction",
        max_length=120,
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

    @field_validator("ingredients")
    @classmethod
    def sanitize_ingredients(cls, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized = []
        for item in items[:50]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            name = re.sub(r'[\r\n\t]+', ' ', name)
            name = name[:60].strip()
            if not name:
                continue
            cleaned = dict(item)
            cleaned["name"] = name
            sanitized.append(cleaned)
        return sanitized

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


class RecipeStepsResponse(BaseModel):
    steps: List[str] = Field(default_factory=list)



