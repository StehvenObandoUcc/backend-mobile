from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

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
        description="Lista de ingredientes del inventario del usuario",
    )
    max_prep_time: int = Field(
        default=30,
        description="Tiempo máximo de preparación en minutos",
    )
    focus: str = Field(
        default="waste_reduction",
        description="Enfoque: 'waste_reduction' (aprovechar alimentos), 'quick' (rápidas), o 'healthy'",
    )
    difficulty: Optional[str] = Field(
        default="any",
        description="Nivel de dificultad: 'easy', 'medium', 'hard', o 'any'",
    )
    count: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Número de recetas que el usuario solicita generar",
    )


class RecipeStepsRequest(BaseModel):
    title: str
    description: Optional[str] = ""
    availableIngredients: List[Dict[str, Any]] = Field(default_factory=list)
    missingIngredients: List[Dict[str, Any]] = Field(default_factory=list)
    difficulty: Optional[str] = "easy"


class RecipeStepsResponse(BaseModel):
    steps: List[str] = Field(default_factory=list)


