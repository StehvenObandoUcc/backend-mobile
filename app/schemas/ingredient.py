from typing import Optional, Union, Literal, List
import re
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, ConfigDict, AliasChoices

ALLOWED_CATEGORIES = {
    'fruit', 'vegetable', 'protein', 'dairy', 'grain', 'legume', 'sauce', 'snack', 'other'
}
ALLOWED_UNITS = {
    'units', 'grams', 'kilograms', 'milliliters', 'liters', 'package', 'unknown'
}
ALLOWED_EXPIRATION_SOURCES = {
    'manual', 'label', 'estimated', 'unknown'
}


class IngredientBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=60, description="Nombre específico del alimento")
    category: str = "other"
    quantity: Optional[Union[int, float]] = Field(default=None, ge=0, le=99999, description="Cantidad estrictamente no negativa")
    unit: Optional[str] = None
    expirationDate: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("expirationDate", "expiration_date"),
        description="Formato YYYY-MM-DD",
    )
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    source: Literal["ai", "manual"] = "ai"
    confirmed: bool = True
    imageUri: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("imageUri", "image_uri"),
        description="URI o URL de foto del alimento",
    )
    notes: Optional[str] = Field(default=None, max_length=500, description="Notas personales del alimento")
    expirationSource: Optional[str] = Field(
        default="unknown",
        validation_alias=AliasChoices("expirationSource", "expiration_source"),
        description="Origen de la fecha",
    )

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

    @field_validator("expirationSource", mode="before")
    @classmethod
    def validate_expiration_source(cls, v):
        if not isinstance(v, str) or v.lower() not in ALLOWED_EXPIRATION_SOURCES:
            return "unknown"
        return v.lower()


class IngredientItem(IngredientBase):
    """Modelo completo para ingredientes persistidos y respuestas de API."""
    id: str = Field(description="Identificador único del ingrediente")
    userId: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("userId", "user_id"),
        description="ID del usuario propietario",
    )
    createdAt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("createdAt", "created_at"),
    )
    updatedAt: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("updatedAt", "updated_at"),
    )


class IngredientCreate(IngredientBase):
    """Modelo para crear un nuevo ingrediente (id opcional, se genera si falta)."""
    id: Optional[str] = None


class IngredientUpdate(BaseModel):
    """Modelo para actualizar un ingrediente existente."""
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[Union[int, float]] = None
    unit: Optional[str] = None
    expirationDate: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("expirationDate", "expiration_date"),
    )
    confidence: Optional[float] = None
    source: Optional[Literal["ai", "manual"]] = None
    confirmed: Optional[bool] = None
    imageUri: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("imageUri", "image_uri"),
    )
    notes: Optional[str] = None
    expirationSource: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("expirationSource", "expiration_source"),
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
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
        if v is None:
            return None
        if not isinstance(v, str) or v.lower() not in ALLOWED_CATEGORIES:
            return "other"
        return v.lower()

    @field_validator("unit", mode="before")
    @classmethod
    def validate_unit(cls, v):
        if v is None:
            return None
        if not isinstance(v, str) or v.lower() not in ALLOWED_UNITS:
            return None
        return v.lower()


class BatchDeleteRequest(BaseModel):
    ids: List[str] = Field(min_length=1, description="Lista de IDs de ingredientes a eliminar")
