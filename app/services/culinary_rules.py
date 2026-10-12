"""Reglas culinarias deterministas para las recetas de la IA. Cero red: solo reglas.

Clasifica cada ingrediente (dulce, bebida, proteína salada, condimento salado, neutro, verdura) y rechaza las
combinaciones que ningún recetario trae: «sardinas al café», «ensalada de piña con cereal de chocolate».
El rechazo entra al reintento correctivo existente de `AIRecipeService.generate_recipes` (sin bucles nuevos).

Reglas (sobre TODOS los ingredientes: disponibles y faltantes):
  R1 dulce-solo + proteína salada      R2 dulce-solo + condimento salado
  R3 bebida + proteína salada          R4 perfil de sabor (`flavorProfile`) contra los ingredientes

Excepciones de platos tradicionales: ver TRADITIONAL_EXCEPTIONS (constante fácil de ampliar).
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.recipe_verifier import _norm, has_term, safe_id

SWEET_ONLY = "SWEET_ONLY"
BEVERAGE = "BEVERAGE"
SAVORY_PROTEIN = "SAVORY_PROTEIN"
SAVORY_CONDIMENT = "SAVORY_CONDIMENT"
NEUTRAL = "NEUTRAL"
VEGETABLE = "VEGETABLE"

# Palabras clave (español e inglés; se normalizan: minúsculas, sin tildes). La palabra clave gana sobre la categoría
# de la app y, entre varias coincidencias, gana la frase más larga («leche condensada» > «leche»).
_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    SWEET_ONLY: (
        "chocolate", "chocolatina", "cacao", "galleta", "cereal", "leche condensada", "gelatina", "mermelada",
        "arequipe", "dulce de leche", "helado", "chantilly", "crema chantilly", "nutella", "bocadillo",
        "masmelo", "malvavisco", "azúcar glas", "chispas de chocolate", "cookie", "jam", "jelly",
        "condensed milk", "ice cream", "marshmallow",
    ),
    BEVERAGE: (
        "café", "té", "gaseosa", "refresco", "malta", "pony malta", "jugo en caja", "coffee", "tea", "soda",
        # Bebidas comerciales que ya maneja ai_recipe_service.COMMERCIAL_BEVERAGES (una prueba vigila que sigan sincronizadas)
        "coca cola", "pepsi", "sprite", "fanta", "cerveza",
    ),
    SAVORY_PROTEIN: (
        "carne", "res", "cerdo", "pollo", "pechuga", "pescado", "atún", "sardina", "salmón", "tilapia", "camarón",
        "marisco", "salchicha", "chorizo", "jamón", "tocino", "embutido", "morcilla", "costilla",
        "pavo", "cordero", "langostino", "calamar", "trucha", "mojarra", "chicharrón", "hamburguesa",
        "beef", "pork", "chicken", "fish", "tuna", "sardine", "shrimp", "sausage", "ham", "bacon", "turkey", "lamb",
    ),
    SAVORY_CONDIMENT: (
        "salsa de soya", "salsa de soja", "mostaza", "mayonesa", "pepinillo", "vinagre", "ajo", "cebolla",
        "ketchup", "salsa de tomate", "salsa inglesa",
        "soy sauce", "mustard", "mayonnaise", "pickle", "vinegar", "garlic", "onion",
    ),
    NEUTRAL: (
        "huevo", "leche", "harina", "mantequilla", "arroz", "avena", "queso", "plátano", "maduro", "fruta",
        "yogur", "yogurt", "crema de leche", "limón", "miel", "canela", "azúcar", "sal", "aceite", "agua",
        # Frases que evitan un falso positivo por una palabra más corta
        "cereal de arroz", "cereal de avena", "cereal integral", "granola", "bicarbonato de soda",
        "egg", "milk", "flour", "butter", "rice", "oats", "cheese", "banana", "fruit", "lemon", "honey",
        "cinnamon", "sugar", "salt", "oil", "water",
    ),
}
# Desempate entre coincidencias igual de largas (mayor = gana)
_PRIORITY = {SWEET_ONLY: 4, SAVORY_PROTEIN: 3, SAVORY_CONDIMENT: 2, BEVERAGE: 1, NEUTRAL: 0}


@dataclass(frozen=True)
class _Term:
    text: str
    category: str
    words: int


_TERMS: Tuple[_Term, ...] = tuple(
    _Term(_norm(term), category, len(_norm(term).split()))
    for category, terms in _KEYWORDS.items()
    for term in terms
)


def _classify(name: Any, app_category: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """(categoría, palabra clave que decidió; None si decidió la categoría de la app)."""
    text = _norm(name)
    matches = [t for t in _TERMS if has_term(text, t.text)]
    if matches:
        # «Sardinas en salsa de tomate» / «atún en aceite»: la proteína manda sobre condimento, bebida o neutro
        proteins = [t for t in matches if t.category == SAVORY_PROTEIN]
        if proteins and all(t.category in (SAVORY_PROTEIN, SAVORY_CONDIMENT, BEVERAGE, NEUTRAL) for t in matches):
            matches = proteins
        best = max(matches, key=lambda t: (t.words, len(t.text), _PRIORITY[t.category]))
        return best.category, best.text
    cat = str(app_category or "").strip().lower()
    if cat == "protein":
        return SAVORY_PROTEIN, None
    if cat in ("vegetable", "legume"):
        return VEGETABLE, None
    return NEUTRAL, None


def classify_ingredient(name: str, app_category: Optional[str] = None) -> str:
    """Categoría culinaria de un ingrediente. La palabra clave gana sobre la categoría de la app.

    Sin coincidencia: `protein` -> SAVORY_PROTEIN, `vegetable`/`legume` -> VEGETABLE, el resto -> NEUTRAL.
    """
    return _classify(name, app_category)[0]


# ── Excepciones de platos tradicionales ──
@dataclass(frozen=True)
class _TradDish:
    """Plato tradicional que justifica una mezcla. Aplica si el título o `dishNameEn` contiene TODAS las palabras
    de algún grupo de `keywords`. Cubre R1 (dulce + proteína) y R4 (perfil de sabor) solo para los ingredientes
    de `sweet` y `other`; R2 y R3 no se excusan nunca."""
    name: str
    keywords: Tuple[Tuple[str, ...], ...]
    sweet: Tuple[str, ...]
    other: Tuple[str, ...]
    rules: Tuple[str, ...] = ("R1", "R4")
    # Con "R2" en `rules`: cualquier condimento salado excusa la mezcla con `sweet` (el mole lleva ajo y cebolla)


_PORK_CHICKEN = ("cerdo", "pollo", "salchicha", "jamon", "tocino", "pork", "chicken", "sausage", "ham", "bacon")

TRADITIONAL_EXCEPTIONS: Tuple[_TradDish, ...] = (
    _TradDish("mole", (("mole",),), ("chocolate", "cacao"), ("pollo", "pechuga", "cerdo", "chicken", "pork"),
              rules=("R1", "R2", "R4")),
    # Hawaiana / agridulce: la piña y las frutas son NEUTRAL, así que normalmente no chocan; la excepción es para la
    # mermelada de piña (la miel y el azúcar tampoco chocan: son NEUTRAL).
    _TradDish("hawaiana", (("hawaiana",), ("hawaiian",)), ("mermelada", "jam", "jelly"), _PORK_CHICKEN),
    _TradDish("agridulce", (("agridulce",), ("sweet", "sour")), ("mermelada", "jam", "jelly"), _PORK_CHICKEN),
    _TradDish(
        "costillas bbq",
        (("costilla", "bbq"), ("costilla", "barbecue"), ("costilla", "barbacoa"), ("rib", "bbq"), ("rib", "barbecue")),
        ("mermelada", "jam", "jelly"),
        ("costilla", "cerdo", "res", "pork", "beef", "rib"),
    ),
)
# Limitación conocida: «vinagre» es condimento salado (R2), así que un bizcocho de chocolate que lleve vinagre se rechaza.
# Se deja así a propósito (decisión del dueño); si hiciera falta, bastaría con una excepción con rules=("R2",).
# Sin excepción, porque no chocan: «miel mostaza» (la miel es NEUTRAL), «maduro con queso» y «bocadillo con queso»
# (el queso es NEUTRAL: ni R1 ni R2 aplican).


def _active_exceptions(recipe: Any) -> List[_TradDish]:
    text = _norm(f"{getattr(recipe, 'title', '')} {getattr(recipe, 'dishNameEn', '') or ''}")
    return [
        exc for exc in TRADITIONAL_EXCEPTIONS
        if any(all(has_term(text, w) for w in group) for group in exc.keywords)
    ]


def _any_term(text: str, terms: Sequence[str]) -> bool:
    return any(has_term(text, t) for t in terms)


def _excuses(exc: _TradDish, rule: str, sweet_norm: str, other_norm: str) -> bool:
    """¿La excepción `exc` excusa la pareja (dulce, otro) en la regla `rule`? Única fuente para check_culinary y la sanidad."""
    if rule not in exc.rules or not _any_term(sweet_norm, exc.sweet):
        return False
    if rule == "R2":
        return classify_ingredient(other_norm) == SAVORY_CONDIMENT
    return _any_term(other_norm, exc.other)


def pair_excused(title: Any, dish_name_en: Any, a: str, b: str) -> bool:
    """¿Una excepción tradicional del título/dishNameEn excusa la pareja a+b (en cualquier orden y en R1 o R2)?
    La usa validate_recipe_sanity para dejar pasar pares como {chocolate, pollo} en un mole."""
    class _R:  # solo necesita title y dishNameEn
        pass
    r = _R()
    r.title, r.dishNameEn = title, dish_name_en
    na, nb = _norm(a), _norm(b)
    return any(
        _excuses(e, rule, x, y)
        for e in _active_exceptions(r) for rule in ("R1", "R2") for x, y in ((na, nb), (nb, na))
    )


# ── Reglas ──
_LABEL = {
    SWEET_ONLY: "dulce", BEVERAGE: "bebida", SAVORY_PROTEIN: "proteína salada", SAVORY_CONDIMENT: "condimento salado",
}
_HINT = (
    "Usa solo ingredientes que formen un plato clásico; si no hay dos que combinen, "
    "usa uno solo como protagonista."
)


@dataclass
class _Item:
    name: str
    norm: str
    category: str


def _items(recipe: Any, pantry: Optional[List[Dict[str, Any]]]) -> List[_Item]:
    by_id = {
        safe_id(p.get("id")): p.get("category")
        for p in (pantry or []) if isinstance(p, dict) and safe_id(p.get("id"))
    }
    out: List[_Item] = []
    for ing in list(getattr(recipe, "availableIngredients", []) or []) + list(getattr(recipe, "missingIngredients", []) or []):
        if not isinstance(ing, dict) or not str(ing.get("name") or "").strip():
            continue
        name = str(ing["name"]).strip()
        app_cat = ing.get("category") or by_id.get(safe_id(ing.get("inventoryIngredientId")))
        out.append(_Item(name, _norm(name), classify_ingredient(name, app_cat)))
    return out


def _pair_reason(a: _Item, b: _Item) -> str:
    return (
        f"Combinación no culinaria: '{a.name.lower()}' ({_LABEL[a.category]}) con '{b.name.lower()}' "
        f"({_LABEL[b.category]}). {_HINT}"
    )


def check_culinary(recipe: Any, pantry: Optional[List[Dict[str, Any]]] = None) -> Tuple[bool, Optional[str], Optional[str]]:
    """(ok, regla, motivo). `regla` es 'R1'..'R4' y `motivo` va en español, accionable para el reintento."""
    items = _items(recipe, pantry)
    excs = _active_exceptions(recipe)

    def pairs(cat_a: str, cat_b: str):
        for a in (i for i in items if i.category == cat_a):
            for b in (i for i in items if i.category == cat_b):
                yield a, b

    def excused_pair(rule: str, a: _Item, b: _Item) -> bool:
        return any(_excuses(e, rule, a.norm, b.norm) for e in excs)

    for rule, cat_a, cat_b in (("R1", SWEET_ONLY, SAVORY_PROTEIN), ("R2", SWEET_ONLY, SAVORY_CONDIMENT), ("R3", BEVERAGE, SAVORY_PROTEIN)):
        for a, b in pairs(cat_a, cat_b):
            if not excused_pair(rule, a, b):
                return False, rule, _pair_reason(a, b)

    profile = getattr(recipe, "flavorProfile", None)
    if profile in ("savory", "sweet"):
        bad_cats = (SWEET_ONLY,) if profile == "savory" else (SAVORY_PROTEIN, SAVORY_CONDIMENT)
        for item in items:
            if item.category not in bad_cats:
                continue
            # Una excepción excusa el ingrediente solo si la receta también trae la pareja que la define
            # (mole: chocolate/cacao Y pollo/cerdo; BBQ: mermelada Y cerdo/res)
            if any(
                "R4" in e.rules and _any_term(item.norm, e.sweet + e.other)
                and any(_any_term(i.norm, e.sweet) for i in items) and any(_any_term(i.norm, e.other) for i in items)
                for e in excs
            ):
                continue
            wanted, other = ("salada", "dulce") if profile == "savory" else ("dulce", "salado")
            return False, "R4", (
                f"Perfil de sabor incoherente: la receta es {wanted} (flavorProfile '{profile}') pero incluye "
                f"'{item.name.lower()}' ({_LABEL[item.category]}). Hazla 100% {wanted}: deja fuera lo {other} "
                "o cambia de plato."
            )
    return True, None, None


def _pair_ok(a: str, b: str) -> bool:
    """¿Dos categorías pueden ir juntas sin violar R1–R3? Una bebida solo se junta con neutros (café con leche)."""
    kinds = {a, b}
    if kinds in ({SWEET_ONLY, SAVORY_PROTEIN}, {SWEET_ONLY, SAVORY_CONDIMENT}, {BEVERAGE, SAVORY_PROTEIN}):
        return False
    if BEVERAGE in kinds and kinds - {BEVERAGE} != {NEUTRAL}:
        return False
    return True


def has_compatible_pair(pantry: Optional[List[Dict[str, Any]]]) -> bool:
    """¿Hay dos ingredientes de la despensa que juntos no violan R1–R3? Si no, basta usar uno solo (min_used=1).

    Decisión del dueño (regla extra, más estricta que R1–R3): una bebida (café, té, gaseosa...) solo se considera
    compatible con ingredientes NEUTRAL (café con leche); con dulces, saludos, verduras u otra bebida no. Así
    gelatina + café + sardinas da False y se permite un solo protagonista. Fallar aquí es barato: solo relaja min_used a 1.
    """
    cats = [
        classify_ingredient(str(p["name"]), p.get("category"))
        for p in (pantry or []) if isinstance(p, dict) and str(p.get("name") or "").strip()
    ]
    return any(_pair_ok(cats[i], cats[j]) for i in range(len(cats)) for j in range(i + 1, len(cats)))
