from typing import Optional, Union, Literal
from pydantic import BaseModel, Field, field_validator

ALLOWED_CATEGORIES = {
    'fruit', 'vegetable', 'protein', 'dairy', 'grain', 'legume', 'sauce', 'snack', 'other'
}
ALLOWED_UNITS = {
    'units', 'grams', 'kilograms', 'milliliters', 'liters', 'package', 'unknown'
}

class IngredientItem(BaseModel):
    id: str = Field(description="Identificador único del ingrediente")
    name: str = Field(min_length=1, description="Nombre específico del alimento")
    category: str = "other"
    quantity: Optional[Union[int, float]] = None      # None real si la IA no lo cuantifica
    unit: Optional[str] = None                        # None real si la IA no especifica unidad
    expirationDate: Optional[str] = None              # Formato YYYY-MM-DD si se detecta o estima
    confidence: Optional[float] = None                # None real si no hay score del modelo
    source: Literal["ai", "manual"] = "ai"
    confirmed: bool = False

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, v):
        if not isinstance(v, str) or v.lower() not in ALLOWED_CATEGORIES:
            return "other"
        return v.lower()

    @field_validator("unit", mode="before")
    @classmethod
    def validate_unit(cls, v):
        if not v or not isinstance(v, str) or v.lower() not in ALLOWED_UNITS:
            return None
        return v.lower()
