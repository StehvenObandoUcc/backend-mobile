# Food AI — Backend API

Backend mínimo construido con FastAPI para la aplicación móvil **Food AI**. Permite validar la conectividad de la aplicación móvil y el flujo de escaneo de alimentos con respuestas estructuradas simuladas (sin llamadas externas a modelos de IA en esta fase inicial).

---

## Requisitos

- Python 3.11 o superior instalado en el sistema.

---

## Instalación y Configuración (Windows PowerShell)

1. **Abrir PowerShell** y ubicarse en la carpeta `backend`:
   ```powershell
   cd backend
   ```

2. **Crear el entorno virtual**:
   ```powershell
   py -3.11 -m venv .venv
   ```
   *(o simplemente `python -m venv .venv` según la configuración de tu sistema)*

3. **Activar el entorno virtual**:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
   > *Nota:* Si Windows bloquea la ejecución de scripts, puedes habilitarla temporalmente con:
   > `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

4. **Instalar dependencias**:
   ```powershell
   pip install -r requirements.txt
   ```

---

## Ejecución del Servidor

Inicia el servidor de desarrollo Uvicorn:
```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

> **Host 0.0.0.0:** Permite que tu teléfono móvil físico o emulador pueda acceder a la API a través de la IP local de tu computadora (por ejemplo, `http://192.168.1.X:8000`).

### Detener el Servidor
Presiona `Ctrl + C` en la terminal para detener el proceso de Uvicorn.

---

## Documentación Interactiva (Swagger / OpenAPI)

Una vez iniciado el servidor, puedes explorar y probar los endpoints desde tu navegador:
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## Endpoints Disponibles

### 1. Health Check
- **Método:** `GET`
- **Ruta:** `/api/v1/health`
- **Descripción:** Verifica el estado operativo de la API.
- **Respuesta exitosa (200 OK):**
  ```json
  {
    "status": "ok",
    "service": "food-ai-backend"
  }
  ```

### 2. Escaneo de Alimentos (Simulado)
- **Método:** `POST`
- **Ruta:** `/api/v1/scan`
- **Content-Type:** `multipart/form-data`
- **Parámetro obligatorio:** `image` (archivo binario de imagen).
- **Tipos MIME soportados:** `image/jpeg`, `image/png`, `image/webp`.
- **Respuesta exitosa (200 OK):**
  ```json
  {
    "scan_id": "demo-scan",
    "ingredients": [
      {
        "id": "demo-1",
        "name": "Tomate",
        "category": "vegetable",
        "quantity": 4,
        "unit": "units",
        "confidence": 0.9,
        "source": "ai",
        "confirmed": false,
        "expirationDate": null
      }
    ],
    "warnings": []
  }
  ```
- **Errores comunes:**
  - `400 Bad Request`: Si el archivo no corresponde a una imagen JPEG, PNG o WebP.
  - `422 Unprocessable Entity`: Si falta el campo `image` en la solicitud multipart.

---

## Ejemplos de Prueba

### Prueba con Swagger UI
1. Abre [http://localhost:8000/docs](http://localhost:8000/docs).
2. Despliega `POST /api/v1/scan` y haz clic en **"Try it out"**.
3. Selecciona una fotografía (`.jpg`, `.png` o `.webp`) en el campo `image`.
4. Presiona **"Execute"** y revisa el JSON de respuesta devuelto con código `200`.

### Prueba con cURL
```bash
curl -X POST "http://localhost:8000/api/v1/scan" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "image=@ruta/a/tu/foto.jpg"
```

---

## Ejecución de Pruebas Automatizadas

Con el entorno virtual activado:
```powershell
pytest
```
