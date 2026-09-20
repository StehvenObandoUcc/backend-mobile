import asyncio
import base64
import json
import os
import sys

# Añadir backend al path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings

# 1x1 base64 png
sample_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

async def run_tests():
    print("=" * 60)
    print("PRUEBAS DE INTEGRACIÓN: ENDPOINTS DE BACKEND CON DEEPSEEK")
    print(f"Modelo configurado: {settings.DEEPSEEK_MODEL}")
    print("=" * 60)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Health check
        print("\n[Test 1/4] GET /api/v1/health...")
        res = await ac.get("/api/v1/health")
        print(f"Status: {res.status_code}, Body: {res.json()}")
        assert res.status_code == 200, "Health check falló"

        # 2. POST /api/v1/scan con imagen no comestible (1x1 transparente)
        print("\n[Test 2/4] POST /api/v1/scan (Guardrail no-comestible)...")
        scan_payload = {
            "image_base64": sample_b64,
            "mime_type": "image/png"
        }
        res = await ac.post("/api/v1/scan", json=scan_payload)
        print(f"Status: {res.status_code}")
        data = res.json()
        print(f"Respuesta Scan:\n{json.dumps(data, indent=2, ensure_ascii=False)}")
        assert res.status_code == 200, f"Scan falló con status {res.status_code}"
        assert "is_food" in data, "Falta is_food en la respuesta"
        print("[OK] is_food verificado en respuesta de /scan.")

        # 3. POST /api/v1/recipes/generate
        print("\n[Test 3/4] POST /api/v1/recipes/generate...")
        recipe_payload = {
            "ingredients": [
                {"name": "Huevos", "quantity": 3, "unit": "units"},
                {"name": "Tomate", "quantity": 2, "unit": "units"},
                {"name": "Queso", "quantity": 100, "unit": "grams"}
            ],
            "max_prep_time": 20,
            "focus": "quick",
            "count": 2
        }
        res = await ac.post("/api/v1/recipes/generate", json=recipe_payload)
        print(f"Status: {res.status_code}")
        if res.status_code == 200:
            recipes = res.json()
            print(f"[OK] {len(recipes)} receta(s) generada(s) por DeepSeek:")
            for r in recipes:
                print(f"  - {r.get('title')} ({r.get('prepTimeMinutes')} min, score: {r.get('matchScore')}%)")
        else:
            print(f"[ERROR] {res.text}")
            assert False, "Fallo al generar recetas"

        # 4. POST /api/v1/recipes (Alias)
        print("\n[Test 4/4] POST /api/v1/recipes (Alias de generate)...")
        res_alias = await ac.post("/api/v1/recipes", json=recipe_payload)
        print(f"Status Alias: {res_alias.status_code}")
        assert res_alias.status_code == 200, "Alias /recipes falló"
        print("[OK] Alias /recipes funciona correctamente.")

    print("\n" + "=" * 60)
    print("TODAS LAS PRUEBAS DE ENDPOINTS PASARON EXITOSAMENTE.")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_tests())
