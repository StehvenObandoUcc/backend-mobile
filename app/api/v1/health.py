from fastapi import APIRouter, status
from pydantic import BaseModel

class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "food-ai-backend"

router = APIRouter()

@router.get("/health", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def check_health() -> HealthResponse:
    """Verifica el estado del servicio."""
    return HealthResponse()
