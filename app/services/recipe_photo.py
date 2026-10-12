"""Fase 3a · Foto real verificada para cada receta.

La foto es SIEMPRE real (Unsplash); nunca se generan imágenes con IA ni se muestra una foto sin verificar.
Cero gasto nuevo: Unsplash en modo demo (50 búsquedas/hora para toda la app) y una única llamada con costo,
la verificación de visión con DeepSeek, limitada por VISION_DAILY_CAP.

Una foto solo se asigna si la visión la aprobó: sin verificación (tope agotado, sin clave, error o respuesta
inválida) la receta queda sin foto.

Nunca hay bucles: cada recurso tiene un tope fijo (máx. 2 búsquedas y 1 llamada de visión por receta, un
presupuesto de tiempo por cada llamada a attach_photos y bloqueo de 10 minutos ante error/límite de Unsplash, sin reintentos).

NOTA: el contador de visión, las cachés y el bloqueo viven en memoria del proceso; si el dyno de Heroku
reinicia (o hay varios dynos) se reinician. Es aceptable: solo significaría gastar un poco más de visión.
"""
import asyncio
import json
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.schemas.recipe import RecipeResponse

logger = logging.getLogger(__name__)

UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"
UNSPLASH_TIMEOUT_SECONDS = 8.0
DOWNLOAD_PING_TIMEOUT_SECONDS = 3.0
VISION_TIMEOUT_SECONDS = 15.0
BLOCK_SECONDS = 10 * 60
CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_MAX_ENTRIES = 500
RESULTS_PER_QUERY = 5
VISION_MAX_TOKENS = 1024
VISION_LOG_CONTENT_CHARS = 120

# Términos que, si la foto los menciona y no son protagonistas ni parte del plato, delatan un plato equivocado:
# proteínas, huevo y tipos de plato distintos
CONTRADICTING_PROTEINS = (
    "chicken", "beef", "pork", "bacon", "ham", "fish", "salmon", "tuna",
    "shrimp", "prawn", "lamb", "turkey", "sausage",
    "egg", "omelette", "omelet",
    "sandwich", "burger", "pizza", "sushi", "taco", "cake", "cookie", "dessert",
    "pancake", "waffle", "donut",
)

# ── Estado en memoria (ver NOTA del docstring) ──
_POSITIVE_CACHE: "OrderedDict[Tuple, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_NEGATIVE_CACHE: "OrderedDict[Tuple, Tuple[float, str]]" = OrderedDict()  # clave -> (instante, motivo)
_vision_state: Dict[str, Any] = {"day": "", "count": 0}
_blocked_until: float = 0.0


def _reset_state_for_tests() -> None:
    """Limpia cachés, contador de visión y bloqueo de Unsplash."""
    global _blocked_until
    _POSITIVE_CACHE.clear()
    _NEGATIVE_CACHE.clear()
    _vision_state["day"] = ""
    _vision_state["count"] = 0
    _blocked_until = 0.0


def _normalize(value: Any) -> str:
    """Minúsculas y sin tildes."""
    text = unicodedata.normalize("NFD", str(value or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def _word_variants(term: str) -> List[str]:
    """Forma singular y plurales simples: egg/eggs, tomato/tomatoes, berry/berries."""
    variants = [term, term + "s", term + "es"]
    if term.endswith("y") and len(term) > 2:
        variants.append(term[:-1] + "ies")
    return variants


def _mentions(text: str, term: str) -> bool:
    return any(re.search(r"(?<![a-z])" + re.escape(v) + r"(?![a-z])", text) for v in _word_variants(term))


# ── Bloqueo de Unsplash y tope de visión ──
def _is_blocked() -> bool:
    return time.time() < _blocked_until


def _vision_has_quota() -> bool:
    """True si hay clave de DeepSeek y queda cupo de visión hoy (no reserva nada)."""
    if not settings.DEEPSEEK_API_KEY:
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = _vision_state["count"] if _vision_state["day"] == today else 0
    return used < settings.VISION_DAILY_CAP


def photos_available() -> bool:
    """True si hay clave de Unsplash, no está bloqueado y se puede verificar con visión.

    Sin verificación de visión no se asigna ninguna foto, así que buscar sería gastar en vano.
    """
    return bool(settings.UNSPLASH_ACCESS_KEY) and not _is_blocked() and _vision_has_quota()


def _block_unsplash(reason: str) -> None:
    global _blocked_until
    _blocked_until = time.time() + BLOCK_SECONDS
    logger.warning(f"[RecipePhoto] Unsplash bloqueado por {BLOCK_SECONDS // 60} min ({reason}); no se busca más.")


def _reserve_vision_call() -> bool:
    """Reserva (síncrono, sin await: seguro entre tareas paralelas) una llamada de visión del día UTC."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _vision_state["day"] != today:
        _vision_state["day"] = today
        _vision_state["count"] = 0
    if _vision_state["count"] >= settings.VISION_DAILY_CAP:
        return False
    _vision_state["count"] += 1
    return True


def _release_vision_call() -> None:
    """Devuelve la unidad reservada por _reserve_vision_call (no hubo candidata que enviar a la visión)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _vision_state["day"] == today and _vision_state["count"] > 0:
        _vision_state["count"] -= 1


# ── Cachés (24 h, máx. 500 entradas; se descarta la más vieja) ──
def _cache_key(query: str, mains: List[str]) -> Tuple:
    return (_normalize(query).strip(), tuple(sorted(_normalize(m).strip() for m in mains)))


def _cache_put(cache: "OrderedDict", key: Tuple, value: Any) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > CACHE_MAX_ENTRIES:
        cache.popitem(last=False)


def _positive_get(key: Tuple) -> Optional[Dict[str, Any]]:
    entry = _POSITIVE_CACHE.get(key)
    if entry is None:
        return None
    if time.time() - entry[0] > CACHE_TTL_SECONDS:
        del _POSITIVE_CACHE[key]
        return None
    return entry[1]


def _negative_get(key: Tuple) -> Optional[str]:
    """Motivo guardado en la caché negativa, o None si no hay entrada vigente."""
    entry = _NEGATIVE_CACHE.get(key)
    if entry is None:
        return None
    if time.time() - entry[0] > CACHE_TTL_SECONDS:
        del _NEGATIVE_CACHE[key]
        return None
    return entry[1]


def alt_text_for(image_uri: Optional[str]) -> Optional[str]:
    """Texto alternativo de una foto aprobada (para revisión manual en scripts/eval_recipes.py)."""
    for _, photo in _POSITIVE_CACHE.values():
        if photo.get("imageUri") == image_uri:
            return photo.get("alt")
    return None


# ── Unsplash ──
def _unsplash_headers() -> Dict[str, str]:
    return {"Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}", "Accept-Version": "v1"}


def _utm(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source={settings.UNSPLASH_APP_NAME}&utm_medium=referral"


async def _search(client: httpx.AsyncClient, query: str) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Una búsqueda en Unsplash. Devuelve (resultados, motivo de fallo); sin reintentos.

    Motivo: "unsplash_bloqueado" (401/403/429) o "unsplash_error" (red, 5xx, JSON inválido).
    """
    try:
        resp = await client.get(
            UNSPLASH_SEARCH_URL,
            headers=_unsplash_headers(),
            params={
                "query": query,
                "per_page": RESULTS_PER_QUERY,
                "orientation": "landscape",
                "content_filter": "high",
            },
            timeout=UNSPLASH_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[RecipePhoto] Falló la búsqueda en Unsplash ({type(exc).__name__}); sin reintento.")
        return None, "unsplash_error"

    if resp.status_code in (401, 403, 429):
        _block_unsplash(f"HTTP {resp.status_code}")
        return None, "unsplash_bloqueado"
    if str(resp.headers.get("X-Ratelimit-Remaining", "")).strip() == "0":
        # La respuesta sirve, pero ya no queda cupo: no se busca más durante el bloqueo
        _block_unsplash("cupo agotado")
    if resp.status_code != 200:
        logger.warning(f"[RecipePhoto] Unsplash respondió HTTP {resp.status_code}; sin reintento.")
        return None, "unsplash_error"
    try:
        results = resp.json().get("results", [])
    except Exception:  # noqa: BLE001
        return None, "unsplash_error"
    if not isinstance(results, list):
        return None, "unsplash_error"
    return [r for r in results if isinstance(r, dict)][:RESULTS_PER_QUERY], None


def _photo_text(photo: Dict[str, Any]) -> str:
    parts = [photo.get("description"), photo.get("alt_description")]
    tags = photo.get("tags")
    if isinstance(tags, list):
        parts.extend(t.get("title") for t in tags if isinstance(t, dict))
    return _normalize(" ".join(str(p) for p in parts if p))


def _covers(word: str, term: str) -> bool:
    """True si `word` es el término o una variante simple suya (egg/eggs)."""
    return word == term or word in _word_variants(term) or term in _word_variants(word)


def _photo_id(photo: Dict[str, Any]) -> str:
    """Identificador de la foto (id; si falta, su URL regular)."""
    return str(photo.get("id") or (photo.get("urls") or {}).get("regular") or "")


def _filter_candidates(results: List[Dict[str, Any]], mains: List[str], dish: str = "") -> List[Tuple[Dict[str, Any], int]]:
    """Filtro de texto (gratis). Devuelve [(foto, puntaje)] ordenado por puntaje desc (empate: orden original).

    Solo sirve para elegir la candidata que verá la visión; no aprueba nada por sí mismo.
    """
    exempt_words = {w for m in mains for w in re.findall(r"[a-z]+", _normalize(m))}
    exempt_words |= set(re.findall(r"[a-z]+", _normalize(dish)))
    ranked: List[Tuple[Dict[str, Any], int]] = []
    for photo in results:
        urls = photo.get("urls")
        if not isinstance(urls, dict) or not urls.get("regular") or not urls.get("small"):
            continue
        text = _photo_text(photo)
        score = sum(1 for m in mains if _mentions(text, _normalize(m)))
        if score == 0:
            continue
        contradicts = any(
            _mentions(text, term) and not any(_covers(w, term) for w in exempt_words)
            for term in CONTRADICTING_PROTEINS
        )
        if contradicts:
            continue
        ranked.append((photo, score))
    ranked.sort(key=lambda item: -item[1])  # sort es estable: el empate conserva el orden original
    return ranked


# ── Visión (único gasto) ──
class _VisionParseError(ValueError):
    """Respuesta de visión inutilizable (ya registrada en el log con su diagnóstico)."""


def _strip_code_fence(text: str) -> str:
    match = re.match(r"^```[A-Za-z0-9_-]*[ \t]*\n?(.*?)\n?```$", text.strip(), re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def _first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Primer objeto `{...}` balanceado del texto que sea JSON válido (respeta cadenas y escapes)."""
    start = text.find("{")
    while start != -1:
        depth, in_str, escaped = 0, False, False
        for pos in range(start, len(text)):
            ch = text[pos]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start:pos + 1])
                    except ValueError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    return None


def _parse_vision_content(raw: str) -> Dict[str, Any]:
    """JSON de la respuesta: directo, sin cercas markdown o el primer objeto balanceado. Vacío = fallo."""
    text = _strip_code_fence(raw or "")
    if not text:
        raise ValueError("Contenido vacío")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        parsed = _first_json_object(text)
    if parsed is None:
        raise ValueError("Sin objeto JSON en el contenido")
    return parsed


async def _vision_approves(client: httpx.AsyncClient, photo: Dict[str, Any], dish: str, mains: List[str]) -> bool:
    """Una llamada a DeepSeek con la miniatura. Lanza excepción si falla (red/formato)."""
    prompt = (
        f'Plato esperado: "{dish}". Ingredientes protagonistas: {", ".join(mains)}.\n'
        "Mira la foto. ¿Muestra un plato de comida preparado y es plausible como ese plato con esos protagonistas?\n"
        'Devuelve EXCLUSIVAMENTE un objeto JSON: {"is_food_dish": true, "matches": true, "reason": "motivo breve"}'
    )
    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": photo["urls"]["small"], "detail": "low"}},
            ],
        }],
        "max_tokens": VISION_MAX_TOKENS,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    resp = await client.post(
        f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=VISION_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    finish_reason: Any = None
    content = ""
    try:
        choice = resp.json()["choices"][0]
        finish_reason = choice.get("finish_reason")
        content = choice["message"].get("content") or ""
        parsed = _parse_vision_content(content)
        if not isinstance(parsed.get("is_food_dish"), bool) or not isinstance(parsed.get("matches"), bool):
            raise ValueError("Respuesta de visión sin los campos booleanos esperados")
    except Exception as exc:  # noqa: BLE001
        # Diagnóstico: solo tipo de error, finish_reason y el inicio del contenido (nunca cabeceras ni claves)
        logger.warning(
            f"[RecipePhoto] Visión inválida: error={type(exc).__name__} finish_reason={finish_reason} "
            f"contenido={str(content)[:VISION_LOG_CONTENT_CHARS]!r}"
        )
        raise _VisionParseError(type(exc).__name__) from exc
    return parsed["is_food_dish"] is True and parsed["matches"] is True


def _build_photo_data(photo: Dict[str, Any]) -> Dict[str, Any]:
    user = photo.get("user") if isinstance(photo.get("user"), dict) else {}
    links = user.get("links") if isinstance(user.get("links"), dict) else {}
    profile = links.get("html")
    source_url = _utm("https://unsplash.com/")
    return {
        "photoId": _photo_id(photo),
        "imageUri": photo["urls"]["regular"],
        "alt": photo.get("alt_description") or photo.get("description") or "",
        "credit": {
            "source": "unsplash",
            "name": str(user.get("name") or "Unsplash"),
            "profileUrl": _utm(profile) if isinstance(profile, str) and profile else source_url,
            "sourceUrl": source_url,
        },
    }


async def _ping_download(client: httpx.AsyncClient, photo: Dict[str, Any]) -> None:
    """La guía de Unsplash pide avisar la descarga al usar una foto. Si falla, se ignora."""
    location = (photo.get("links") or {}).get("download_location") if isinstance(photo.get("links"), dict) else None
    if not location:
        return
    # La clave solo viaja a la API de Unsplash: https y host exacto (nunca a otro dominio)
    try:
        parsed = urlparse(str(location))
        if parsed.scheme != "https" or parsed.hostname != "api.unsplash.com":
            return
    except ValueError:
        return
    try:
        await client.get(location, headers=_unsplash_headers(), timeout=DOWNLOAD_PING_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001
        pass


def _apply(recipe: RecipeResponse, data: Dict[str, Any]) -> None:
    recipe.imageUri = data["imageUri"]
    recipe.imageCredit = dict(data["credit"])


# ── Flujo por receta ──
async def _pick_candidate(
    client: httpx.AsyncClient, dish: str, fallback_query: str, mains: List[str], key: Tuple,
    stats: Dict[str, int], used: set,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Escalera de consultas y elección de candidata. Devuelve (foto reservada, motivo si no hay)."""
    # (a) nombre del plato; (b) 2 protagonistas + «dish». Máximo 2 búsquedas.
    queries = [q for q in (dish, fallback_query) if q]
    if len(queries) == 2 and _normalize(queries[0]) == _normalize(queries[1]):
        queries = queries[:1]

    saw_candidates = False
    for query in queries[:2]:
        if _is_blocked():
            return None, "unsplash_bloqueado"  # no se cachea como «sin foto»: no fue un rechazo real
        results, failure = await _search(client, query)
        stats["searches"] += 1
        if results is None:
            return None, failure or "unsplash_error"  # error o límite: se corta sin reintentar ni cachear
        ranked = _filter_candidates(results, mains, dish)
        saw_candidates = saw_candidates or bool(ranked)
        # Primera candidata no usada en el lote; se reserva sin await entre comprobar y reservar
        for photo, _score in ranked:
            pid = _photo_id(photo)
            if pid not in used:
                used.add(pid)
                return photo, ""
    if saw_candidates:
        return None, "repetida"  # todas las candidatas ya están en uso en este lote: no se cachea
    _cache_put(_NEGATIVE_CACHE, key, (time.time(), "sin_candidatas"))
    return None, "sin_candidatas"


async def _process_recipe(
    recipe: RecipeResponse, client: httpx.AsyncClient, stats: Dict[str, int], used: set,
    reasons: List[str], index: int,
) -> str:
    """Procesa una receta y devuelve el motivo del resultado (para diagnóstico en scripts/eval_recipes.py).

    `used` son los ids de foto ya asignados o reservados en este lote. Al aprobar, el motivo se fija en
    `reasons[index]` antes de cualquier await posterior, para que un corte de tiempo no lo pise.
    """
    mains = [m for m in recipe.mainIngredientsEn if m][:3]
    if not mains:
        return "sin_protagonistas"
    if _is_blocked():
        return "unsplash_bloqueado"

    dish = (recipe.dishNameEn or "").strip()
    fallback_query = " ".join(mains[:2]) + " dish"
    key = _cache_key(dish or fallback_query, mains)

    cached = _positive_get(key)
    if cached is not None:
        stats["cache_hits"] += 1
        if cached.get("photoId") in used:
            return "repetida"
        used.add(cached.get("photoId"))
        _apply(recipe, cached)
        reasons[index] = "aprobada"
        return "aprobada"
    negative = _negative_get(key)
    if negative is not None:
        stats["cache_hits"] += 1
        return negative

    # Sin verificación de visión no se asigna foto: el cupo se reserva ANTES de buscar para no gastar
    # búsquedas de Unsplash para nada (síncrono, sin await: seguro entre tareas paralelas)
    if not settings.DEEPSEEK_API_KEY or not _reserve_vision_call():
        return "sin_cupo"

    slot_used = False
    try:
        chosen, reason = await _pick_candidate(client, dish, fallback_query, mains, key, stats, used)
        if chosen is None:
            return reason
        slot_used = True  # la unidad se queda gastada aunque la visión falle
    finally:
        if not slot_used:
            _release_vision_call()  # no hubo candidata que enviar a la visión: se devuelve la unidad

    stats["vision_calls"] += 1
    try:
        approved = await _vision_approves(client, chosen, dish or fallback_query, mains)
    except Exception as exc:  # noqa: BLE001
        stats["vision_failed"] += 1
        if not isinstance(exc, _VisionParseError):  # los errores de formato ya se registraron con detalle
            logger.warning(f"[RecipePhoto] Visión falló ({type(exc).__name__}); la receta queda sin foto.")
        return "vision_fallo"  # no se cachea: podrá verificarse más tarde

    if not approved:
        _cache_put(_NEGATIVE_CACHE, key, (time.time(), "vision_no"))
        return "vision_no"

    data = _build_photo_data(chosen)
    _cache_put(_POSITIVE_CACHE, key, (time.time(), data))
    stats["approved_vision"] += 1
    _apply(recipe, data)
    reasons[index] = "aprobada"
    await _ping_download(client, chosen)
    return "aprobada"


async def _safe_process(
    recipe: RecipeResponse, client: httpx.AsyncClient, stats: Dict[str, int], used: set, reasons: List[str], index: int
) -> None:
    try:
        reasons[index] = await _process_recipe(recipe, client, stats, used, reasons, index)
    except Exception as exc:  # noqa: BLE001
        reasons[index] = "error"
        logger.warning(f"[RecipePhoto] Error con la foto de una receta ({type(exc).__name__}: {str(exc)[:120]}); queda sin foto.")


async def attach_photos(
    recipes: List[RecipeResponse],
    http_client: Optional[httpx.AsyncClient] = None,
    reasons: Optional[List[str]] = None,
    used_ids: Optional[set] = None,
) -> Dict[str, int]:
    """Busca y verifica con visión una foto real para cada receta, modificándolas en el sitio.

    Nunca lanza excepciones hacia afuera: ante cualquier problema las recetas quedan sin foto.
    Devuelve estadísticas {searches, cache_hits, vision_calls, approved_vision, vision_failed, no_photo}.
    Si se pasa `reasons` (lista), se rellena con el motivo de cada receta, en el mismo orden.
    Si se pasa `used_ids`, ese conjunto de ids de foto se usa y se actualiza (para no repetir fotos entre
    varias llamadas de una misma respuesta).
    """
    stats = {"searches": 0, "cache_hits": 0, "vision_calls": 0, "approved_vision": 0, "vision_failed": 0, "no_photo": 0}
    local_reasons: List[str] = []
    try:
        items = list(recipes or [])
        local_reasons = ["tiempo_agotado"] * len(items)
        used: set = used_ids if used_ids is not None else set()  # fotos asignadas o reservadas
        if items and settings.UNSPLASH_ACCESS_KEY:
            async def _run(client: httpx.AsyncClient) -> None:
                await asyncio.wait_for(
                    asyncio.gather(*(_safe_process(r, client, stats, used, local_reasons, i) for i, r in enumerate(items))),
                    timeout=settings.PHOTO_TIME_BUDGET_SECONDS,
                )

            try:
                if http_client is not None:
                    await _run(http_client)
                else:
                    async with httpx.AsyncClient() as own_client:
                        await _run(own_client)
            except asyncio.TimeoutError:
                logger.warning("[RecipePhoto] Presupuesto de tiempo agotado; las recetas pendientes quedan sin foto.")
        else:
            local_reasons = ["unsplash_bloqueado"] * len(items)  # sin clave de Unsplash no hay fotos
        stats["no_photo"] = sum(1 for r in items if not getattr(r, "imageUri", None))
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[RecipePhoto] Error inesperado al adjuntar fotos ({type(exc).__name__}); recetas sin foto.")
    if reasons is not None:
        try:
            reasons[:] = local_reasons
        except Exception:  # noqa: BLE001
            pass
    return stats
