from fastapi import APIRouter
from app.api.v1 import health, scan, recipes, auth

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(scan.router, tags=["scan"])
api_router.include_router(recipes.router, tags=["recipes"])
api_router.include_router(auth.router)

