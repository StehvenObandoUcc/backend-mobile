from typing import List, Optional
from pydantic import BaseModel, Field
from .ingredient import IngredientItem


class ScanBase64Request(BaseModel):
    image_base64: str = Field(..., description="Cadena de imagen en formato base64")
    mime_type: Optional[str] = Field("image/jpeg", description="Tipo MIME de la imagen")


class ScanResponse(BaseModel):
    scan_id: str
    is_food: bool = Field(..., description="Indica si la imagen corresponde a alimentos/bebidas. Requerido sin default.")
    ingredients: List[IngredientItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
