from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, AliasChoices


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

    id: Optional[str] = None
    name: str = Field(min_length=1, max_length=100)
    quantity: Optional[float] = None
    unit: Optional[str] = "units"
    category: Optional[str] = "other"
    isBought: Optional[bool] = Field(default=False, validation_alias=AliasChoices("isBought", "is_bought"))
    recipeSource: Optional[str] = Field(default=None, validation_alias=AliasChoices("recipeSource", "recipe_source"))


class ShoppingItemUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    isBought: Optional[bool] = Field(default=None, validation_alias=AliasChoices("isBought", "is_bought"))
    recipeSource: Optional[str] = Field(default=None, validation_alias=AliasChoices("recipeSource", "recipe_source"))


class ShoppingBatchCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: List[ShoppingItemCreate]
