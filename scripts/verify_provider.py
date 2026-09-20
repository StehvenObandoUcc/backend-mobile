import os
import sys
import time
import json
import httpx

# Cargar variables de backend/.env sin dependencias externas
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(backend_dir, ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
model_name = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

print("=" * 60)
print("VERIFICACIÓN DE PROVEEDOR: DEEPSEEK")
print(f"Base URL: {base_url}")
print(f"Modelo objetivo: {model_name}")
print(f"API Key presente: {'Sí (' + api_key[:6] + '...' + api_key[-4:] + ')' if api_key else 'NO'}")
print("=" * 60)

if not api_key:
    print("[ERROR] No se encontró API Key en backend/.env")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}

# 1. Verificar GET /models
print("\n[Paso 1/2] Consultando GET /models...")
try:
    with httpx.Client(timeout=20.0) as client:
        resp = client.get(f"{base_url}/models", headers=headers)
        print(f"Status Code: {resp.status_code}")
        if resp.status_code != 200:
            print(f"[FALLO] La API rechazó la consulta de modelos: {resp.text}")
            sys.exit(1)
        
        models_data = resp.json().get("data", [])
        model_ids = [m.get("id") for m in models_data]
        print(f"Modelos disponibles ({len(model_ids)}): {model_ids}")
        
        if model_name in model_ids:
            print(f"[OK] Modelo '{model_name}' está disponible en tu cuenta.")
        else:
            print(f"[AVISO] '{model_name}' no aparece explícitamente en /models, pero probaremos si el endpoint lo admite.")
except Exception as e:
    print(f"[ERROR] Excepción conectando a /models: {e}")
    sys.exit(1)

# 2. Prueba de visión mínima con imagen en base64
print("\n[Paso 2/2] Enviando llamada de prueba visual (multimodal)...")
# PNG de 1x1 píxel transparente para validar estructura
sample_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

payload = {
    "model": model_name,
    "response_format": {"type": "json_object"},
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Analiza esta imagen. Responde estrictamente en formato json con esta estructura: "
                        "{\"is_food\": false, \"description\": \"breve descripcion\"}"
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{sample_b64}"
                    },
                },
            ],
        }
    ],
    "max_tokens": 150,
    "temperature": 0.1,
}

start_time = time.time()
try:
    with httpx.Client(timeout=45.0) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        elapsed = round(time.time() - start_time, 2)
        print(f"Status Code: {resp.status_code} (Latencia: {elapsed}s)")
        
        if resp.status_code == 200:
            result = resp.json()
            content = result["choices"][0]["message"]["content"]
            print(f"[OK] Respuesta recibida exitosamente:\n{content}")
            parsed = json.loads(content)
            print("[OK] JSON válido parseado correctamente.")
            print("\n" + "=" * 60)
            print("VERIFICACIÓN EXITOSA: El modelo soporta llamadas multimodales en JSON.")
            print("=" * 60)
        else:
            print(f"[FALLO] Error HTTP {resp.status_code}:\n{resp.text}")
            sys.exit(1)
except Exception as e:
    print(f"[ERROR] Excepción en llamada visual: {e}")
    sys.exit(1)
