"""Verificación determinista de recetas generadas por IA contra la despensa real del usuario.

Fase 1 del plan «Recetas confiables + foto real». Cero llamadas a red: solo reglas.

Principio: la identidad de un ingrediente se decide por su **id** (`inventoryIngredientId`),
no por parecido de nombres. Lo reparable se repara; solo se rechaza lo que no se puede arreglar
(ingrediente a evitar, receta sin ingredientes reales, uso mínimo no alcanzado).
"""
import logging
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Básicos que una receta puede dar por supuestos aunque no estén en la despensa.
STAPLES = {
    "sal", "agua", "pimienta", "aceite", "azucar", "vinagre", "oregano", "comino", "canela",
    "laurel", "tomillo", "romero", "salt", "water", "pepper", "oil", "sugar",
}
STOP_WORDS = {"de", "la", "el", "en", "y", "a", "los", "las", "un", "una", "del", "al", "con", "sin", "para", "por"}

# Unidad -> (familia, factor a la unidad base g / ml).
_UNIT_FACTOR = {
    "grams": ("mass", 1.0),
    "kilograms": ("mass", 1000.0),
    "milliliters": ("volume", 1.0),
    "liters": ("volume", 1000.0),
}

_ID_RE = re.compile(r"[^A-Za-z0-9_\-:.]")


def safe_id(value: Any) -> str:
    """Id apto para ir dentro de un prompt: solo caracteres inocuos y longitud acotada (evita inyección)."""
    return _ID_RE.sub("", str(value or ""))[:64]


def _norm(text: Any) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(c for c in value if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value)).strip()


def _variants(word: str) -> set:
    """Formas posibles del singular: tomates -> tomate; limones -> limon."""
    out = {word}
    if len(word) > 3 and word.endswith("s"):
        out.add(word[:-1])
    if len(word) > 4 and word.endswith("es"):
        out.add(word[:-2])
    return out


def _tokens(text: Any) -> List[set]:
    return [_variants(w) for w in _norm(text).split() if len(w) >= 3 and w not in STOP_WORDS]


def _any_overlap(ta: List[set], tb: List[set]) -> bool:
    return any(x & y for x in ta for y in tb)


def names_overlap(a: Any, b: Any) -> bool:
    """¿Comparten una palabra significativa? (se usa para ingredientes a evitar, donde sobrar es lo seguro)."""
    return _any_overlap(_tokens(a), _tokens(b))


def is_staple(name: Any) -> bool:
    return _any_overlap(_tokens(name), [_variants(s) for s in STAPLES])


def _same_name(a: Any, b: Any) -> bool:
    """Igualdad estricta de nombre completo (ignorando tildes, mayúsculas y plural simple)."""
    ta, tb = _norm(a).split(), _norm(b).split()
    if not ta or not tb or len(ta) != len(tb):
        return False
    return all(_variants(x) & _variants(y) for x, y in zip(ta, tb))


def _convert(qty: float, src: str, dst: str) -> Optional[float]:
    if src == dst:
        return qty
    a, b = _UNIT_FACTOR.get(src), _UNIT_FACTOR.get(dst)
    if not a or not b or a[0] != b[0]:
        return None
    return qty * a[1] / b[1]


@dataclass
class VerifyResult:
    ok: bool
    reason: Optional[str] = None
    repairs: List[str] = field(default_factory=list)

    def __iter__(self):  # permite `ok, reason = verify_recipe(...)`
        yield self.ok
        yield self.reason


def _reject(reason: str, repairs: List[str]) -> VerifyResult:
    return VerifyResult(False, reason, repairs)


def verify_recipe(
    recipe: Any,
    pantry: List[Dict[str, Any]],
    avoid: Optional[List[str]] = None,
    min_used: int = 2,
) -> VerifyResult:
    """Revisa y repara `recipe` (RecipeResponse) en el sitio contra la despensa elegida por el usuario."""
    avoid = [a for a in (avoid or []) if _norm(a)]
    repairs: List[str] = []

    chosen = [p for p in pantry if isinstance(p, dict) and p.get("name")]
    by_id = {safe_id(p.get("id")): p for p in chosen if safe_id(p.get("id"))}

    # 1) Ingredientes a evitar (seguridad): ni en título, descripción ni ingredientes. Rechazo duro.
    texts = [recipe.title, recipe.description] + [
        i.get("name", "") for i in (recipe.availableIngredients + recipe.missingIngredients) if isinstance(i, dict)
    ]
    for term in avoid:
        for text in texts:
            if names_overlap(term, text):
                return _reject(f"Incluye «{term}», que el usuario pidió evitar (en: {str(text)[:50]})", repairs)

    if not chosen:
        # Sin ingredientes elegidos (modo de respaldo del backend): solo rige la seguridad de arriba.
        return VerifyResult(True, None, repairs)

    # 2) Cada «disponible» debe ser un ingrediente real de la despensa elegida (por id).
    kept: List[Dict[str, Any]] = []
    moved: List[Dict[str, Any]] = []
    used_ids: Dict[str, float] = {}  # id -> cantidad ya asignada, en la unidad de la despensa
    pantry_backed = 0

    for ing in recipe.availableIngredients:
        if not isinstance(ing, dict):
            continue
        name = ing.get("name", "")
        claimed = safe_id(ing.get("inventoryIngredientId") or ing.get("pantryId"))
        match = by_id.get(claimed) if claimed else None
        if match is not None and not names_overlap(name, match.get("name")):
            # El id existe pero apunta a OTRO alimento (la IA se equivocó al copiarlo): no se confía en él.
            repairs.append(f"id de «{name}» apunta a «{match.get('name')}»: descartado")
            match = None

        if match is None:
            # Respaldo estricto: nombre completo idéntico (no parecido) a UN ingrediente elegido.
            same = [p for p in chosen if _same_name(name, p.get("name"))]
            if len(same) == 1:
                match = same[0]
                repairs.append(f"id asignado por nombre exacto: «{name}»")

        if match is None:
            if is_staple(name):
                ing["inventoryIngredientId"] = None  # básico supuesto: no cuenta como ingrediente de la despensa
                kept.append(ing)
                continue
            # Inventado o fuera de lo elegido: pasa a faltante (no se rechaza la receta entera).
            ing["isAvailable"] = False
            ing["inventoryIngredientId"] = None
            moved.append(ing)
            repairs.append(f"«{name}» no estaba en la despensa elegida: movido a faltantes")
            continue

        mid = safe_id(match.get("id"))
        ing["inventoryIngredientId"] = mid or None
        ing["isAvailable"] = True

        # Cantidad: nunca más de lo que hay (acumulado por ingrediente, con conversión g↔kg y ml↔L).
        stock, qty = match.get("quantity"), ing.get("quantity")
        already = used_ids.get(mid, 0.0)
        if isinstance(stock, (int, float)) and isinstance(qty, (int, float)) and stock >= 0:
            wanted = _convert(qty, str(ing.get("unit")), str(match.get("unit")))
            if wanted is not None:
                allowed = max(stock - already, 0.0)
                if wanted > allowed * 1.0001:
                    back = _convert(allowed, str(match.get("unit")), str(ing.get("unit")))
                    if back is not None and back > 0:
                        ing["quantity"] = round(back, 1)
                        wanted = allowed
                        repairs.append(f"cantidad de «{name}» recortada a lo que hay")
                used_ids[mid] = already + wanted
            else:
                used_ids.setdefault(mid, already)
        else:
            used_ids.setdefault(mid, already)
        pantry_backed += 1
        kept.append(ing)

    if not pantry_backed:
        return _reject("Ningún ingrediente «disponible» pertenece a la despensa elegida", repairs)

    # 3) Uso mínimo de lo elegido (si eligió menos, basta con usar todo lo elegido).
    distinct = len({safe_id(i.get("inventoryIngredientId")) for i in kept if i.get("inventoryIngredientId")})
    needed = min(min_used, len(chosen))
    if distinct < needed:
        return _reject(f"Usa solo {distinct} de los ingredientes elegidos; debe usar al menos {needed}", repairs)

    recipe.availableIngredients = kept
    recipe.missingIngredients = list(recipe.missingIngredients) + moved

    # 4) matchScore real: ingredientes de la despensa / (esos + faltantes obligatorios).
    required_missing = [m for m in recipe.missingIngredients if isinstance(m, dict) and not m.get("isOptional")]
    total = pantry_backed + len(required_missing)
    recipe.matchScore = max(1, min(100, math.floor(100 * pantry_backed / total))) if total else 1

    if repairs:
        logger.info("[Verifier] reparaciones en «%s»: %s", recipe.title, "; ".join(repairs))
    return VerifyResult(True, None, repairs)
