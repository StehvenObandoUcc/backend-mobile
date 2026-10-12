"""Evaluación REAL de la Fase 1 (recetas verificadas por id) contra DeepSeek.

Uso (desde la carpeta backend, con tu .env y el entorno virtual activos):
    python scripts/eval_recipes.py            # 8 despensas (≈ 8-16 llamadas a DeepSeek)
    python scripts/eval_recipes.py --n 15     # las 15 despensas
    python scripts/eval_recipes.py --n 3      # prueba rápida
    python scripts/eval_recipes.py --n 8 --photos   # además busca y verifica la foto de cada receta

Con --photos (requiere UNSPLASH_ACCESS_KEY; sin ella solo avisa y sigue sin fotos): Unsplash en modo demo
permite 50 búsquedas/hora y 15 despensas × 2 recetas × hasta 2 búsquedas pueden pasarse; se recomienda
`--n 8 --photos`. La verificación de visión gasta tokens de DeepSeek (tope VISION_DAILY_CAP).

No imprime ni guarda claves. Escribe el resumen en scripts/eval_recipes_result.json
(sin datos personales: solo nombres de ingredientes de prueba y contadores).
Consume tokens de DeepSeek: cada despensa pide 2 recetas (1 llamada, 2 si hay reintento).
"""
import argparse
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.schemas.recipe import RecipeGenerateRequest  # noqa: E402
from app.services import ai_recipe_service  # noqa: E402
from app.services.recipe_photo import alt_text_for, attach_photos  # noqa: E402
from app.services.ai_recipe_service import AIRecipeService  # noqa: E402
from app.services.recipe_verifier import names_overlap  # noqa: E402


def P(*items):
    """Despensa de prueba: (nombre, cantidad, unidad)."""
    return [
        {"id": f"t{i}", "name": n, "quantity": q, "unit": u, "category": "other"}
        for i, (n, q, u) in enumerate(items, 1)
    ]


SCENARIOS = [
    ("huevos-tomate", P(("Huevos", 6, "units"), ("Tomates", 500, "grams"), ("Cebolla", 2, "units")), []),
    ("pollo-arroz", P(("Pechuga de pollo", 600, "grams"), ("Arroz", 500, "grams"), ("Zanahoria", 3, "units")), []),
    ("solo-aguacate", P(("Aguacate", 2, "units")), []),
    ("salmon-limon", P(("Salmón", 400, "grams"), ("Limones", 3, "units"), ("Ajo", 4, "units")), []),
    ("vegetariano", P(("Lentejas", 300, "grams"), ("Papa", 4, "units"), ("Espinaca", 200, "grams")), []),
    ("lacteos", P(("Leche", 1, "liters"), ("Queso mozzarella", 250, "grams"), ("Pasta", 400, "grams")), []),
    ("con-evitar-mani", P(("Pollo", 500, "grams"), ("Brócoli", 300, "grams"), ("Arroz", 300, "grams")), ["maní", "camarón"]),
    ("res-papa", P(("Carne de res", 500, "grams"), ("Papas", 5, "units"), ("Cebolla larga", 2, "units")), []),
    ("frutas", P(("Plátano maduro", 3, "units"), ("Avena", 200, "grams"), ("Leche", 500, "milliliters")), []),
    ("bebida-trampa", P(("Pony Malta", 2, "units"), ("Pollo", 500, "grams"), ("Cebolla", 1, "units")), []),
    ("dulce", P(("Chocolate oscuro", 100, "grams"), ("Huevos", 4, "units"), ("Harina", 500, "grams")), []),
    ("sopa", P(("Yuca", 500, "grams"), ("Cilantro", 1, "units"), ("Costilla de res", 400, "grams"), ("Mazorca", 2, "units")), []),
    ("pocos-g-kg", P(("Arroz", 0.5, "kilograms"), ("Frijoles", 250, "grams"), ("Chorizo", 2, "units")), []),
    ("pescado", P(("Filete de tilapia", 500, "grams"), ("Pimentón", 2, "units"), ("Tomate", 2, "units")), []),
    ("ensalada", P(("Lechuga", 1, "units"), ("Pepino", 2, "units"), ("Atún en lata", 2, "units")), []),
]


async def main(n: int, photos: bool = False):
    if not settings.DEEPSEEK_API_KEY:
        print("Falta DEEPSEEK_API_KEY en tu .env. Aborto sin gastar nada.")
        return 1

    if photos and not settings.UNSPLASH_ACCESS_KEY:
        print("Aviso: falta UNSPLASH_ACCESS_KEY en tu .env; se omite --photos (no se harán fotos).")
        photos = False

    stats = {"first_attempt_rejects": 0, "verifier_calls": 0, "repairs": 0}
    photo_stats = {"searches": 0, "cache_hits": 0, "vision_calls": 0, "approved_vision": 0, "vision_failed": 0, "no_photo": 0}
    photo_details = []
    original = ai_recipe_service.verify_recipe

    def counting(recipe, pantry, avoid=None, **kw):
        res = original(recipe, pantry, avoid, **kw)
        stats["verifier_calls"] += 1
        stats["repairs"] += len(res.repairs)
        if not res.ok:
            stats["first_attempt_rejects"] += 1
        return res

    ai_recipe_service.verify_recipe = counting

    # El limitador de 10 peticiones/minuto protege el endpoint en producción; aquí se llama al
    # servicio directo y no es lo que se evalúa, así que se desactiva para no falsear los resultados.
    async def _no_limit(*_a, **_k):
        return None

    ai_recipe_service.check_rate_limit = _no_limit

    rows, total_recipes, invented_after = [], 0, 0
    for name, pantry, avoid in SCENARIOS[:n]:
        req = RecipeGenerateRequest(ingredients=pantry, count=2, avoid_ingredients=avoid, max_prep_time=30)
        t0 = time.time()
        row = {"scenario": name, "ok": False, "seconds": 0.0, "recipes": 0, "error": None}
        try:
            recipes = await AIRecipeService.generate_recipes(req)
            row["ok"], row["recipes"] = True, len(recipes)
            total_recipes += len(recipes)
            if photos:
                reasons: list = []
                for k, v in (await attach_photos(recipes, reasons=reasons)).items():
                    photo_stats[k] += v
                for r, reason in zip(recipes, reasons):
                    photo_details.append({
                        "scenario": name,
                        "title": r.title,
                        "dishNameEn": r.dishNameEn,
                        "mainIngredientsEn": r.mainIngredientsEn,
                        "imageUri": r.imageUri,
                        "motivo": reason,
                        "altText": alt_text_for(r.imageUri) if r.imageUri else None,
                    })
            ids = {p["id"] for p in pantry}
            for r in recipes:
                for ing in r.availableIngredients:
                    pid = ing.get("inventoryIngredientId")
                    if pid and pid not in ids:
                        invented_after += 1
                for term in avoid:
                    if any(names_overlap(term, i.get("name", "")) for i in r.availableIngredients + r.missingIngredients):
                        invented_after += 1
        except HTTPException as exc:
            row["error"] = f"{exc.status_code}: {exc.detail}"
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        print(f"{'OK ' if row['ok'] else 'FALLA'} {name:<18} {row['seconds']:>5}s  recetas={row['recipes']}  {row['error'] or ''}")

    done = len(rows)
    ok = sum(1 for r in rows if r["ok"])
    summary = {
        "despensas": done,
        "despensas_con_recetas": ok,
        "tasa_exito": round(100 * ok / done, 1) if done else 0,
        "recetas_entregadas": total_recipes,
        "ingredientes_inventados_o_prohibidos_tras_verificar": invented_after,
        "rechazos_del_verificador": stats["first_attempt_rejects"],
        "recetas_revisadas": stats["verifier_calls"],
        "tasa_rechazo_verificador_pct": round(100 * stats["first_attempt_rejects"] / stats["verifier_calls"], 1)
        if stats["verifier_calls"] else 0,
        "reparaciones_aplicadas": stats["repairs"],
        "segundos_promedio": round(sum(r["seconds"] for r in rows) / done, 1) if done else 0,
    }
    if photos:
        with_photo = sum(1 for d in photo_details if d["imageUri"])
        summary.update({
            "recetas_con_foto": with_photo,
            "pct_con_foto": round(100 * with_photo / total_recipes, 1) if total_recipes else 0,
            "aprobadas_por_vision": photo_stats["approved_vision"],
            "fallos_vision": photo_stats["vision_failed"],
            "llamadas_vision": photo_stats["vision_calls"],
            "busquedas_unsplash": photo_stats["searches"],
        })
    summary["detalle"] = rows
    if photos:
        # motivo: aprobada / vision_no / vision_fallo / sin_candidatas / sin_cupo / unsplash_bloqueado / repetida /
        # tiempo_agotado / sin_protagonistas (o error). Para revisar a mano si alguna foto es equivocada.
        summary["detalle_fotos"] = photo_details
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_recipes_result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print("\n=== RESUMEN ===")
    for k, v in summary.items():
        if k not in ("detalle", "detalle_fotos"):
            print(f"{k}: {v}")
    print("\nMetas: ingredientes inventados tras verificar = 0 · rechazo del verificador < 10 % · éxito >= 90 %")
    print(f"Resultado guardado en {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="cuántas despensas probar (1-15)")
    ap.add_argument("--photos", action="store_true", help="buscar y verificar la foto de cada receta (Unsplash)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(max(1, min(args.n, len(SCENARIOS))), photos=args.photos)))
