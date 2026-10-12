"""Fase 3a · Foto real verificada para cada receta.

La foto es SIEMPRE real (Unsplash); nunca se generan imágenes con IA ni se muestra una foto sin verificar.
Cero gasto nuevo: Unsplash en modo demo (50 búsquedas/hora para toda la app) y una única llamada con costo,
la verificación de visión con DeepSeek, limitada por VISION_DAILY_CAP.

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

# Proteínas que, si la foto las menciona y no son protagonistas, delatan un plato equivocado
CONTRADICTING_PROTEINS = (
    "chicken", "beef", "pork", "bacon", "ham", "fish", "salmon", "tuna",
    "shrimp", "prawn", "lamb", "turkey", "sausage",
)

# ── Estado en memoria (ver NOTA del docstring) ──
_POSITIVE_CACHE: "OrderedDict[Tuple, Tuple[float, Dict[str, Any]]]" = OrderedDict()
_NEGATIVE_CACHE: "OrderedDict[Tuple, float]" = OrderedDict()
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


def photos_available() -> bool:
    """True si hay clave de Unsplash y no está bloqueado (si no, intentar más fotos sería gastar en vano)."""
    return bool(settings.UNSPLASH_ACCESS_KEY) and not _is_blocked()


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


def _negative_hit(key: Tuple) -> bool:
    ts = _NEGATIVE_CACHE.get(key)
    if ts is None:
        return False
    if time.time() - ts > CACHE_TTL_SECONDS:
        del _NEGATIVE_CACHE[key]
        return False
    return True


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


async def _search(client: httpx.AsyncClient, query: str) -> Optional[List[Dict[str, Any]]]:
    """Una búsqueda en Unsplash. None = error o límite (se corta sin reintentar)."""
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
        return None

    if resp.status_code in (401, 403, 429):
        _block_unsplash(f"HTTP {resp.status_code}")
        return None
    if str(resp.headers.get("X-Ratelimit-Remaining", "")).strip() == "0":
        # La respuesta sirve, pero ya no queda cupo: no se busca más durante el bloqueo
        _block_unsplash("cupo agotado")
    if resp.status_code != 200:
        logger.warning(f"[RecipePhoto] Unsplash respondió HTTP {resp.status_code}; sin reintento.")
        return None
    try:
        results = resp.json().get("results", [])
    except Exception:  # noqa: BLE001
        return None
    return [r for r in results if isinstance(r, dict)][:RESULTS_PER_QUERY] if isinstance(results, list) else None


def _photo_text(photo: Dict[str, Any]) -> str:
    parts = [photo.get("description"), photo.get("alt_description")]
    tags = photo.get("tags")
    if isinstance(tags, list):
        parts.extend(t.get("title") for t in tags if isinstance(t, dict))
    return _normalize(" ".join(str(p) for p in parts if p))


def _filter_candidates(results: List[Dict[str, Any]], mains: List[str]) -> Optional[Tuple[Dict[str, Any], int]]:
    """Filtro de texto (gratis). Devuelve (foto, puntaje) de la mejor candidata o None."""
    protagonist_words = {w for m in mains for w in re.findall(r"[a-z]+", _normalize(m))}
    best: Optional[Tuple[Dict[str, Any], int]] = None
    for photo in results:
        urls = photo.get("urls")
        if not isinstance(urls, dict) or not urls.get("regular") or not urls.get("small"):
            continue
        text = _photo_text(photo)
        score = sum(1 for m in mains if _mentions(text, _normalize(m)))
        if score == 0:
            continue
        contradicts = any(
            _mentions(text, protein) and not any(protein in _word_variants(w) for w in protagonist_words)
            for protein in CONTRADICTING_PROTEINS
        )
        if contradicts:
            continue
        if best is None or score > best[1]:  # empate: gana la primera
            best = (photo, score)
    return best


# ── Visión (único gasto) ──
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
        "max_tokens": 150,
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
    raw = resp.json()["choices"][0]["message"].get("content") or ""
    parsed = json.loads(raw.strip())
    if not isinstance(parsed, dict) or "is_food_dish" not in parsed or "matches" not in parsed:
        raise ValueError("Respuesta de visión sin los campos esperados")
    return parsed["is_food_dish"] is True and parsed["matches"] is True


def _build_photo_data(photo: Dict[str, Any]) -> Dict[str, Any]:
    user = photo.get("user") if isinstance(photo.get("user"), dict) else {}
    links = user.get("links") if isinstance(user.get("links"), dict) else {}
    profile = links.get("html")
    source_url = _utm("https://unsplash.com/")
    return {
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
async def _process_recipe(recipe: RecipeResponse, client: httpx.AsyncClient, stats: Dict[str, int]) -> None:
    mains = [m for m in recipe.mainIngredientsEn if m][:3]
    if not mains or _is_blocked():
        return

    dish = (recipe.dishNameEn or "").strip()
    fallback_query = " ".join(mains[:2]) + " dish"
    key = _cache_key(dish or fallback_query, mains)

    cached = _positive_get(key)
    if cached is not None:
        stats["cache_hits"] += 1
        _apply(recipe, cached)
        return
    if _negative_hit(key):
        stats["cache_hits"] += 1
        return

    # Escalera de consultas: (a) nombre del plato; (b) 2 protagonistas + «dish». Máximo 2 búsquedas.
    queries = [q for q in (dish, fallback_query) if q]
    if len(queries) == 2 and _normalize(queries[0]) == _normalize(queries[1]):
        queries = queries[:1]

    best: Optional[Tuple[Dict[str, Any], int]] = None
    for query in queries[:2]:
        if _is_blocked():
            return  # no se cachea como «sin foto»: no fue un rechazo real
        results = await _search(client, query)
        stats["searches"] += 1
        if results is None:
            return  # error o límite: se corta sin reintentar ni cachear
        best = _filter_candidates(results, mains)
        if best is not None:
            break

    if best is None:
        _cache_put(_NEGATIVE_CACHE, key, time.time())
        return

    photo, score = best
    approved, by_vision = False, False
    vision_available = bool(settings.DEEPSEEK_API_KEY) and _reserve_vision_call()
    if vision_available:
        stats["vision_calls"] += 1
        try:
            approved = await _vision_approves(client, photo, dish or fallback_query, mains)
            by_vision = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[RecipePhoto] Visión falló ({type(exc).__name__}); se aplica la regla de solo texto.")
            vision_available = False
    if not vision_available:
        approved = score >= min(2, len(mains))

    if not approved:
        # Solo el «NO» de la visión se cachea; un rechazo por solo texto podrá verificarse con visión después
        if by_vision:
            _cache_put(_NEGATIVE_CACHE, key, time.time())
        return

    data = _build_photo_data(photo)
    _cache_put(_POSITIVE_CACHE, key, (time.time(), data))
    stats["approved_vision" if by_vision else "approved_text"] += 1
    _apply(recipe, data)
    await _ping_download(client, photo)


async def _safe_process(recipe: RecipeResponse, client: httpx.AsyncClient, stats: Dict[str, int]) -> None:
    try:
        await _process_recipe(recipe, client, stats)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[RecipePhoto] Error con la foto de una receta ({type(exc).__name__}: {str(exc)[:120]}); queda sin foto.")


async def attach_photos(recipes: List[RecipeResponse], http_client: Optional[httpx.AsyncClient] = None) -> Dict[str, int]:
    """Busca y verifica una foto real para cada receta, modificándolas en el sitio.

    Nunca lanza excepciones hacia afuera: ante cualquier problema las recetas quedan sin foto.
    Devuelve estadísticas {searches, cache_hits, vision_calls, approved_vision, approved_text, no_photo}.
    """
    stats = {"searches": 0, "cache_hits": 0, "vision_calls": 0, "approved_vision": 0, "approved_text": 0, "no_photo": 0}
    try:
        if recipes and settings.UNSPLASH_ACCESS_KEY:
            async def _run(client: httpx.AsyncClient) -> None:
                await asyncio.wait_for(
                    asyncio.gather(*(_safe_process(r, client, stats) for r in recipes)),
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
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[RecipePhoto] Error inesperado al adjuntar fotos ({type(exc).__name__}); recetas sin foto.")
    stats["no_photo"] = sum(1 for r in recipes if not r.imageUri)
    return stats
