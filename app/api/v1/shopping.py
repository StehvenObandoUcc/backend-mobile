from typing import List, Optional
from fastapi import APIRouter, Header, HTTPException, status, Depends
from app.schemas.shopping import (
    ShoppingItemSchema,
    ShoppingItemCreate,
    ShoppingItemUpdate,
    ShoppingBatchCreate,
)
from app.services.auth_service import AuthService
from app.core import db

router = APIRouter(prefix="/shopping", tags=["shopping"])


def get_current_user_id(authorization: Optional[str] = Header(None)) -> str:
    """Extrae el ID del usuario desde el Bearer token JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere autenticación para sincronizar la lista de compras.",
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


@router.get("", response_model=List[ShoppingItemSchema], status_code=status.HTTP_200_OK)
async def list_shopping_items(user_id: str = Depends(get_current_user_id)) -> List[ShoppingItemSchema]:
    """Recupera la lista de compras sincronizada del usuario desde la base de datos."""
    items = db.get_shopping_items_by_user(user_id)
    return [ShoppingItemSchema.model_validate(item) for item in items]


@router.post("", response_model=ShoppingItemSchema, status_code=status.HTTP_201_CREATED)
async def add_shopping_item(
    payload: ShoppingItemCreate,
    user_id: str = Depends(get_current_user_id),
) -> ShoppingItemSchema:
    """Agrega y persiste un nuevo artículo en la lista de compras del usuario."""
    data = payload.model_dump(by_alias=True)
    created = db.create_shopping_item(data, user_id=user_id)
    return ShoppingItemSchema.model_validate(created)


@router.post("/batch", response_model=List[ShoppingItemSchema], status_code=status.HTTP_201_CREATED)
async def batch_add_shopping_items(
    payload: ShoppingBatchCreate,
    user_id: str = Depends(get_current_user_id),
) -> List[ShoppingItemSchema]:
    """Agrega múltiples artículos a la lista de compras (ej. desde ingredientes de recetas)."""
    items_data = [item.model_dump(by_alias=True) for item in payload.items]
    created_list = db.batch_create_shopping_items(items_data, user_id=user_id)
    return [ShoppingItemSchema.model_validate(c) for c in created_list]


@router.put("/{item_id}", response_model=ShoppingItemSchema, status_code=status.HTTP_200_OK)
async def update_shopping_item(
    item_id: str,
    payload: ShoppingItemUpdate,
    user_id: str = Depends(get_current_user_id),
) -> ShoppingItemSchema:
    """Actualiza el estado (comprado/pendiente, cantidad, nombre) de un artículo de compras."""
    updates = payload.model_dump(exclude_unset=True, by_alias=True)
    updated = db.update_shopping_item(item_id, user_id, updates)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artículo con ID '{item_id}' no encontrado en tu lista de compras.",
        )
    return ShoppingItemSchema.model_validate(updated)


@router.delete("/bought", status_code=status.HTTP_200_OK)
async def delete_bought_items(user_id: str = Depends(get_current_user_id)):
    """Elimina de la base de datos todos los artículos marcados como comprados."""
    count = db.delete_bought_shopping_items(user_id)
    return {"status": "success", "deleted_count": count}


@router.delete("/{item_id}", status_code=status.HTTP_200_OK)
async def delete_shopping_item(
    item_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Elimina físicamente un artículo de la lista de compras de la base de datos."""
    deleted = db.delete_shopping_item(item_id, user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Artículo con ID '{item_id}' no encontrado en tu lista de compras.",
        )
    return {"status": "deleted", "id": item_id, "message": "Artículo eliminado de la lista de compras."}
