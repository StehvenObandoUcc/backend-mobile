from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, AliasChoices, field_validator

from .ingredient import ALLOWED_CATEGORIES, ALLOWED_UNITS


def _normalize_unit(v):
    if v is None:
        return None
    if not isinstance(v, str) or v.lower() not in ALLOWED_UNITS:
        return "units"
    return v.lower()


def _normalize_category(v):
    if v is None:
        return None
    if not isinstance(v, str) or v.lower() not in ALLOWED_CATEGORIES:
        return "other"
    return v.lower()


def _clean_name(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    if not v:
        raise ValueError("El nombre no puede estar vacío.")
    return v


class ShoppingItemSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    name: str = Field(min_length=1, max_length=100)
    quantity: Optional[float] = None
    unit: str = Field(default="units", max_length=30)
    category: str = Field(default="other", max_length=30)
    isBought: bool = Field(default=False, validation_alias=AliasChoices("isBought", "is_bought"))
    recipeSource: Optional[str] = Field(default=None, validation_alias=AliasChoices("recipeSource", "recipe_source"))
    createdAt: Optional[str] = Field(default=None, validation_alias=AliasChoices("createdAt", "created_at"))


class ShoppingItemCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    quantity: Optional[float] = Field(default=None, ge=0, le=99999)
    unit: Optional[str] = "units"
    category: Optional[str] = "other"
    isBought: Optional[bool] = Field(default=False, validation_alias=AliasChoices("isBought", "is_bought"))
    recipeSource: Optional[str] = Field(
        default=None, max_length=200, validation_alias=AliasChoices("recipeSource", "recipe_source")
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        return _clean_name(v)

    @field_validator("unit", mode="before")
    @classmethod
    def validate_unit(cls, v):
        return _normalize_unit(v) or "units"

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, v):
        return _normalize_category(v) or "other"


class ShoppingItemUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    quantity: Optional[float] = Field(default=None, ge=0, le=99999)
    unit: Optional[str] = None
    category: Optional[str] = None
    isBought: Optional[bool] = Field(default=None, validation_alias=AliasChoices("isBought", "is_bought"))
    recipeSource: Optional[str] = Field(
        default=None, max_length=200, validation_alias=AliasChoices("recipeSource", "recipe_source")
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        return _clean_name(v)

    @field_validator("unit", mode="before")
    @classmethod
    def validate_unit(cls, v):
        return _normalize_unit(v)

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, v):
        return _normalize_category(v)


class ShoppingBatchCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: List[ShoppingItemCreate] = Field(max_length=100)
