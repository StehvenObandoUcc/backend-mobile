import base64
import json
import logging
import uuid
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from app.core.config import settings
from app.core.rate_limit import check_rate_limit, check_ai_rate_limit, get_client_ip
from app.schemas.scan import ScanResponse
from app.schemas.ingredient import IngredientItem

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/jpg",
}


async def _run_deepseek_vision(
    b64_image: str,
    mime_type: str = "image/jpeg",
    http_client: Optional[httpx.AsyncClient] = None,
) -> ScanResponse:
    """Invoca la API multimodal de DeepSeek Flash para analizar alimentos en una imagen."""
    if not settings.DEEPSEEK_API_KEY:
        logger.error("[Scan] DEEPSEEK_API_KEY no está configurada en las variables de entorno.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de análisis con IA no está configurado en el servidor.",
        )

    clean_mime = mime_type if mime_type in ALLOWED_IMAGE_TYPES else "image/jpeg"
    if "," in b64_image:
        b64_image = b64_image.split(",", 1)[1]


    # Prompt condensado: sin relleno conversacional, preservando reglas morfológicas y de confianza
    prompt = (
        "Analiza la imagen e identifica los alimentos con máxima fidelidad visual.\n\n"
        "1. FILTRO DE CONSUMIBLES:\n"
        "Si la imagen NO contiene alimentos o bebidas comestibles, "
        "responde: is_food: false, warnings: ['Esto no parece comida. Intenta con una foto de tu nevera o despensa.'], e ingredients: [].\n\n"
        "2. IDENTIFICACIÓN Y DISCRIMINACIÓN MORFOLÓGICA:\n"
        "Si contiene alimentos, establece is_food: true e identifica los presentes en primer plano:\n"
        "  * Tomate: piel lisa, tersa y brillante, color rojo vivo o anaranjado, forma esférica o globular, posible pedúnculo/cáliz verde en forma de estrella. ¡No confundir con cebolla morada ni pimientos!\n"
        "  * Cebolla morada/roja: piel seca, papirácea con capas visibles, estrías longitudinales marcadas, forma aplanada o cónica con posible raíz fibrosa en la base.\n"
        "  * Ajo: bulbo compuesto de dientes individuales cubiertos por piel seca y fina papelosa.\n"
        "- Nombre específico y común en español.\n"
        "- Categorías válidas: 'vegetable', 'fruit', 'protein', 'dairy', 'grain', 'legume', 'sauce', 'snack', 'other'.\n"
        "- Unidades válidas: 'units', 'grams', 'kilograms', 'milliliters', 'liters', 'package', 'unknown'. Si no puedes estimar con certeza cantidad o unidad, devuelve null.\n"
        "- Fecha de vencimiento (expirationDate): Si está impresa en el empaque, extráela en 'YYYY-MM-DD'. Si es un alimento fresco sin empaque, estima 'YYYY-MM-DD' realista según su vida útil típica.\n"
        "- Confianza (confidence): número decimal entre 0.0 y 1.0 según la certeza y claridad visual. Si tienes dudas razonables sobre la identidad de un alimento, asigna una confianza baja (menor a 0.6) en lugar de adivinar con alta confianza.\n\n"
        "Devuelve EXCLUSIVAMENTE un objeto JSON válido con esta estructura exacta:\n"
        "{\n"
        '  "is_food": true,\n'
        '  "warnings": [],\n'
        '  "ingredients": [\n'
        '    {\n'
        '      "name": "Tomate",\n'
        '      "category": "vegetable",\n'
        '      "quantity": 2,\n'
        '      "unit": "units",\n'
        '      "expirationDate": "2026-09-26",\n'
        '      "confidence": 0.95\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{clean_mime};base64,{b64_image.strip()}"
                        },
                    },
                ],
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{settings.DEEPSEEK_BASE_URL}/chat/completions"

    try:
        if http_client is not None:
            resp = await http_client.post(
                url,
                headers=headers,
                json=payload,
                timeout=settings.DEEPSEEK_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        else:
            async with httpx.AsyncClient() as fallback_client:
                resp = await fallback_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=settings.DEEPSEEK_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
    except httpx.TimeoutException as exc:
        logger.error(f"[Scan] Timeout llamando a DeepSeek ({settings.DEEPSEEK_TIMEOUT_SECONDS}s): {exc}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="La IA tardó demasiado en responder al analizar la foto. Intenta de nuevo.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        logger.error(f"[Scan] Error HTTP de DeepSeek {exc.response.status_code}: {exc.response.text[:300]}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El servicio de IA no pudo procesar la imagen en este momento. Intenta más tarde.",
        ) from exc
    except Exception as exc:
        logger.error(f"[Scan] Error de red o conexión con DeepSeek: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error de conexión con el servicio de IA.",
        ) from exc

    try:
        body = resp.json()
        raw_text = body["choices"][0]["message"].get("content") or ""
        logger.info(f"[Scan] DeepSeek choices[0]: {body.get('choices', [{}])[0]}")
        parsed = json.loads(raw_text.strip())

        # Mapear ingredientes preservando nulls reales (sin inventar defaults)
        raw_ingredients = parsed.get("ingredients", [])
        ingredients = [
            IngredientItem(
                id=f"ingredient-{i+1}",
                name=str(item.get("name", "")).strip(),
                category=item.get("category", "other"),
                quantity=item.get("quantity"),      # None real si no se cuantificó
                unit=item.get("unit"),              # None real si no se especificó
                expirationDate=item.get("expirationDate"),
                confidence=item.get("confidence"),  # None real si no hay score
                source="ai",
                confirmed=False,
            )
            for i, item in enumerate(raw_ingredients)
            if item.get("name")
        ]

        # Validar contrato estricto de ScanResponse (is_food requerido sin default)
        result = ScanResponse(
            scan_id=f"scan-{uuid.uuid4().hex[:6]}",
            is_food=parsed["is_food"],
            ingredients=ingredients,
            warnings=parsed.get("warnings", []),
        )
        return result

    except (json.JSONDecodeError, KeyError, ValidationError) as exc:
        logger.error(f"[Scan] Respuesta de DeepSeek inválida o schema corrupto: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="La respuesta de la IA no tiene un formato válido. Intenta de nuevo.",
        ) from exc


@router.post("/scan", response_model=ScanResponse, status_code=status.HTTP_200_OK)
async def scan_image(request: Request) -> ScanResponse:
    # Control de tasa por cliente/IP y global para proteger contra abusos en fase de pruebas (máximo 5 llamadas/min)
    client_ip = get_client_ip(request)
    await check_ai_rate_limit(f"scan_{client_ip}", max_per_minute=settings.MAX_CALLS_PER_MINUTE)
    await check_rate_limit(settings.MAX_CALLS_PER_MINUTE)


    MAX_IMAGE_PAYLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_IMAGE_PAYLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="La imagen enviada excede el tamaño máximo permitido de 10 MB.",
                )
        except ValueError:
            pass

    content_type = request.headers.get("content-type", "").lower()
    b64_image = ""
    mime_type = "image/jpeg"

    if "application/json" in content_type:
        try:
            body = await request.json()
            b64_image = body.get("image_base64", "")
            mime_type = body.get("mime_type", "image/jpeg")
        except Exception:
            raise HTTPException(status_code=400, detail="Cuerpo JSON no válido")
    elif "multipart/form-data" in content_type:
        try:
            form = await request.form()
            upload = form.get("image")
            if upload and hasattr(upload, "read"):
                file_bytes = await upload.read(MAX_IMAGE_PAYLOAD_BYTES + 1)
                if len(file_bytes) > MAX_IMAGE_PAYLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="El archivo excede el tamaño máximo permitido de 10 MB.",
                    )
                b64_image = base64.b64encode(file_bytes).decode("utf-8")
                mime_type = getattr(upload, "content_type", "image/jpeg")
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"[Scan] Error procesando payload multipart: {exc}")
            raise HTTPException(status_code=400, detail="Error al procesar el archivo multipart.")
    else:
        try:
            body = await request.json()
            b64_image = body.get("image_base64", "")
            mime_type = body.get("mime_type", "image/jpeg")
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Tipo de contenido no soportado. Enviar application/json o multipart/form-data.",
            )

    if not b64_image:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se encontró imagen en la solicitud. Envíe 'image_base64' o campo 'image'.",
        )

    if len(b64_image) > int(MAX_IMAGE_PAYLOAD_BYTES * 1.4):
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="La imagen en base64 excede el tamaño máximo permitido de 10 MB.",
        )

    http_client = getattr(request.app.state, "http_client", None)
    return await _run_deepseek_vision(b64_image, mime_type, http_client=http_client)
