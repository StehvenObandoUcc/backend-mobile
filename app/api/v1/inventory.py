from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, status, Depends
from app.schemas.ingredient import (
    IngredientItem,
    IngredientCreate,
    IngredientUpdate,
    BatchDeleteRequest,
)
from app.services.auth_service import AuthService
from app.core import db

router = APIRouter(prefix="/inventory", tags=["inventory"])


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """Extrae el ID del usuario desde el Bearer token JWT de forma estricta.
    Lanza 401 Unauthorized si el token no existe, está expirado o es inválido."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere autenticación. Proporcione un token Bearer válido en el encabezado Authorization.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split("Bearer ")[1].strip()
    try:
        user = AuthService.get_current_user_from_token(token)
        return user.id
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticación inválido o expirado.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.get("", response_model=List[IngredientItem], status_code=status.HTTP_200_OK)
async def list_inventory(user_id: str = Depends(get_current_user_id)) -> List[IngredientItem]:
    """Obtiene todos los alimentos del inventario del usuario actual desde la base de datos."""
    items = db.get_inventory_by_user(user_id)
    return [IngredientItem.model_validate(item) for item in items]


@router.post("", response_model=IngredientItem, status_code=status.HTTP_201_CREATED)
async def add_ingredient(
    payload: IngredientCreate,
    user_id: str = Depends(get_current_user_id),
) -> IngredientItem:
    """Inserta un nuevo alimento en la base de datos conservando todos sus campos."""
    data = payload.model_dump(by_alias=True)
    created = db.create_ingredient(data, user_id=user_id)
    return IngredientItem.model_validate(created)


@router.put("/{ingredient_id}", response_model=IngredientItem, status_code=status.HTTP_200_OK)
async def update_ingredient(
    ingredient_id: str,
    payload: IngredientUpdate,
    user_id: str = Depends(get_current_user_id),
) -> IngredientItem:
    """Actualiza los campos de un alimento en la base de datos y actualiza updated_at."""
    updates = payload.model_dump(exclude_unset=True, by_alias=True)
    if not updates:
        existing = db.get_ingredient_by_id(ingredient_id, user_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Alimento con ID '{ingredient_id}' no encontrado en tu inventario.",
            )
        return IngredientItem.model_validate(existing)

    updated = db.update_ingredient(ingredient_id, user_id, updates)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alimento con ID '{ingredient_id}' no encontrado en tu inventario.",
        )
    return IngredientItem.model_validate(updated)


@router.delete("/{ingredient_id}", status_code=status.HTTP_200_OK)
async def delete_ingredient(
    ingredient_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Elimina físicamente un alimento de la base de datos verificando que efectivamente se borre."""
    deleted = db.delete_ingredient(ingredient_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alimento con ID '{ingredient_id}' no encontrado en tu inventario.",
        )
    return {"status": "deleted", "id": ingredient_id, "message": "Alimento eliminado de la base de datos con éxito."}


@router.post("/batch-delete", status_code=status.HTTP_200_OK)
async def batch_delete_ingredients(
    payload: BatchDeleteRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Elimina múltiples alimentos del inventario de forma atómica en la base de datos."""
    count = db.delete_ingredients_batch(payload.ids, user_id)
    return {
        "status": "deleted",
        "count": count,
        "deleted_ids": payload.ids,
        "message": f"Se eliminaron {count} alimentos de la base de datos exitosamente.",
    }
