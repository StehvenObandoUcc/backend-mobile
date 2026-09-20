from typing import Optional, Union, Literal
import re
from datetime import datetime
from pydantic import BaseModel, Field, field_validator

ALLOWED_CATEGORIES = {
    'fruit', 'vegetable', 'protein', 'dairy', 'grain', 'legume', 'sauce', 'snack', 'other'
}
ALLOWED_UNITS = {
    'units', 'grams', 'kilograms', 'milliliters', 'liters', 'package', 'unknown'
}

class IngredientItem(BaseModel):
    id: str = Field(description="Identificador único del ingrediente")
    name: str = Field(min_length=1, max_length=60, description="Nombre específico del alimento")
    category: str = "other"
    quantity: Optional[Union[int, float]] = Field(default=None, ge=0, le=99999, description="Cantidad estrictamente no negativa")
    unit: Optional[str] = None                        # None real si la IA no especifica unidad
    expirationDate: Optional[str] = Field(default=None, description="Formato YYYY-MM-DD")
    confidence: Optional[float] = None                # None real si no hay score del modelo
    source: Literal["ai", "manual"] = "ai"
    confirmed: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("El nombre no puede estar vacío.")
        if len(v) > 60:
            raise ValueError("El nombre no puede exceder 60 caracteres.")
        return v

    @field_validator("quantity", mode="before")
    @classmethod
    def validate_quantity(cls, v):
        if v is None or v == "":
            return None
        try:
            val = float(v)
            if val < 0:
                raise ValueError("La cantidad no puede ser negativa.")
            if val > 99999:
                raise ValueError("La cantidad no puede exceder 99,999.")
            return val
        except (ValueError, TypeError) as e:
            raise ValueError("Cantidad inválida.") from e

    @field_validator("expirationDate")
    @classmethod
    def validate_expiration_date(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = v.strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            raise ValueError("La fecha debe tener formato YYYY-MM-DD.")
        try:
            parsed = datetime.strptime(v, "%Y-%m-%d")
            if parsed.year < 2024 or parsed.year > 2099:
                raise ValueError("El año de la fecha debe estar entre 2024 y 2099.")
        except ValueError as e:
            raise ValueError("Fecha de vencimiento inválida.") from e
        return v

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
