"""Evaluación REAL de la Fase 1 (recetas verificadas por id) contra DeepSeek.

Uso (desde la carpeta backend, con tu .env y el entorno virtual activos):
    python scripts/eval_recipes.py            # 8 despensas (≈ 8-16 llamadas a DeepSeek)
    python scripts/eval_recipes.py --n 15     # las 15 despensas
    python scripts/eval_recipes.py --n 3      # prueba rápida
    python scripts/eval_recipes.py --n 8 --photos   # además busca y verifica la foto de cada receta
    python scripts/eval_recipes.py --n 17 --photos  # las 17 primeras (incluye las 2 variadas; las 3 incoherentes van con --incoherentes) (≈ 40 búsquedas)
    python scripts/eval_recipes.py --incoherentes --pasos --photos
        # solo las 3 despensas sin relación entre sí («incoherente-mezcla», «incoherente-extremo», «incoherente-snacks»),
        # con receta completa y pasos. Costo aprox.: 3 despensas x 2 recetas = ~3-6 llamadas de recetas (1-2 por
        # despensa) + hasta 6 de pasos (1 por receta, 2 si hay reintento) + hasta 12 búsquedas de Unsplash.
    python scripts/eval_recipes.py --solo incoherente-mezcla,incoherente-extremo   # nombres separados por coma (ignora --n)
        # un nombre desconocido imprime la lista válida y sale con código 2 sin gastar llamadas.
    --pasos: además llama a generate_recipe_steps por cada receta entregada (si falla, registra el error y sigue).
    En las despensas incoherentes lo correcto es una receta simple con 2-3 ingredientes que sí combinen, o un fallo
    limpio (422); NUNCA una fusión de ingredientes que no tienen nada que ver. Un 422 es un resultado válido.

Con --photos (requiere UNSPLASH_ACCESS_KEY; sin ella solo avisa y sigue sin fotos): Unsplash en modo demo
permite 50 búsquedas/hora y 15 despensas × 2 recetas × hasta 2 búsquedas pueden pasarse; se recomienda
`--n 8 --photos`; cuando haya cupo de Unsplash (≈ 40 búsquedas para las 17 despensas) usa `--n 17 --photos`.
Las dos últimas despensas («variada-dulce-salada» y «variada-grande») miden las fusiones raras: por cada receta
se informa cuántos ingredientes de la despensa usa (meta: 2 a 6) y su dishNameEn. La verificación de visión gasta tokens de DeepSeek (tope VISION_DAILY_CAP).

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
from app.schemas.recipe import RecipeGenerateRequest, RecipeStepsRequest  # noqa: E402
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
    # Despensas variadas (al FINAL para que `--n 8` no cambie): ponen a prueba las fusiones forzadas
    ("variada-dulce-salada", P(
        ("Plátano", 4, "units"), ("Chocolate", 150, "grams"), ("Café molido", 100, "grams"),
        ("Pechuga de pollo", 600, "grams"), ("Arroz", 500, "grams"), ("Tomates", 500, "grams"),
        ("Cebolla", 2, "units"), ("Queso", 250, "grams"), ("Huevos", 6, "units"), ("Leche", 1, "liters"),
    ), []),
    ("variada-grande", P(
        ("Papa", 1, "kilograms"), ("Zanahoria", 4, "units"), ("Cebolla", 3, "units"), ("Ajo", 6, "units"),
        ("Pollo", 800, "grams"), ("Carne molida", 500, "grams"), ("Arroz", 1, "kilograms"), ("Pasta", 500, "grams"),
        ("Huevos", 12, "units"), ("Leche", 1, "liters"), ("Queso", 300, "grams"), ("Tomates", 600, "grams"),
        ("Pimentón", 2, "units"), ("Aguacate", 2, "units"),
    ), []),
    # Despensas incoherentes (ingredientes sin relación entre sí; al FINAL para que --n 8 / --n 17 no cambien)
    ("incoherente-mezcla", P(
        ("Gelatina de fresa", 1, "units"), ("Atún en lata", 2, "units"), ("Café molido", 100, "grams"),
        ("Pepinillos", 200, "grams"), ("Mermelada de mora", 250, "grams"), ("Salsa de soya", 150, "milliliters"),
        ("Galletas de chocolate", 200, "grams"), ("Mostaza", 100, "grams"),
    ), []),
    # Lo correcto: receta simple con 2 que sí combinen, o fallo limpio 422; NUNCA fusión de los tres.
    ("incoherente-extremo", P(
        ("Gelatina de fresa", 1, "units"), ("Sardinas en lata", 2, "units"), ("Café molido", 100, "grams"),
    ), []),
    ("incoherente-snacks", P(
        ("Leche condensada", 1, "units"), ("Salchichas", 6, "units"), ("Piña", 1, "units"),
        ("Mayonesa", 200, "grams"), ("Cereal de chocolate", 300, "grams"), ("Limón", 3, "units"),
    ), []),
]
INCOHERENTES = ["incoherente-mezcla", "incoherente-extremo", "incoherente-snacks"]


def _fmt_ing(i):
    q, u = i.get("quantity"), i.get("unit")
    return f"{i.get('name', '?')}" + (f" ({q} {u})" if q not in (None, "") and u else "")


async def main(selected, photos: bool = False, pasos: bool = False):
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

    # Los límites (por minuto y por usuario) viven en la ruta, no en el servicio: aquí se llama al servicio
    # directo y attach_photos sin user_id (sin tope por usuario), así que no falsean los resultados.
    rows, total_recipes, invented_after = [], 0, 0
    recipe_details = []  # por receta: cuántos ingredientes de la despensa usa y su nombre en inglés
    full_recipes = []  # receta completa por receta entregada
    for name, pantry, avoid in selected:
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
                used_ids = {i.get("inventoryIngredientId") for i in r.availableIngredients}
                full = {
                    "scenario": name,
                    "title": r.title,
                    "description": r.description,
                    "dishNameEn": r.dishNameEn,
                    "availableIngredients": [{"name": i.get("name"), "quantity": i.get("quantity"), "unit": i.get("unit")} for i in r.availableIngredients],
                    "missingIngredients": r.missingIngredients,
                    "ingredientes_de_despensa_no_usados": [p["name"] for p in pantry if p["id"] not in used_ids],
                }
                if pasos:
                    try:
                        steps_req = RecipeStepsRequest(
                            title=r.title,
                            description=r.description or "",
                            availableIngredients=r.availableIngredients,
                            missingIngredients=r.missingIngredients,
                            difficulty=r.difficulty,
                            avoid_ingredients=avoid,
                        )
                        full["pasos"] = await AIRecipeService.generate_recipe_steps(steps_req)
                    except HTTPException as exc:
                        full["pasos"] = None
                        full["pasos_error"] = f"{exc.status_code}: {exc.detail}"
                    except Exception as exc:  # noqa: BLE001
                        full["pasos"] = None
                        full["pasos_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                full_recipes.append(full)
                used = len({i.get("inventoryIngredientId") for i in r.availableIngredients if i.get("inventoryIngredientId")})
                recipe_details.append({
                    "scenario": name,
                    "title": r.title,
                    "dishNameEn": r.dishNameEn,
                    "ingredientes_de_despensa_usados": used,
                    "ingredientes_de_despensa_total": len(pantry),
                })
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
        if name in INCOHERENTES:
            print(f"  Despensa: {', '.join(p['name'] for p in pantry)}")
            if not row["ok"]:
                print(f"  Sin recetas (resultado válido): {row['error']}")
            for fr in (f for f in full_recipes if f["scenario"] == name):
                print(f"  - {fr['title']}  [{fr['dishNameEn']}]")
                print(f"    {fr['description']}")
                print(f"    Usados de la despensa: {', '.join(_fmt_ing(i) for i in fr['availableIngredients']) or '(ninguno)'}")
                print(f"    Faltan: {', '.join(_fmt_ing(i) for i in fr['missingIngredients']) or '(nada)'}")
                print(f"    No usados: {', '.join(fr['ingredientes_de_despensa_no_usados']) or '(ninguno)'}")
                if fr.get("pasos"):
                    for k, st in enumerate(fr["pasos"], 1):
                        print(f"      {k}. {st}")
                elif fr.get("pasos_error"):
                    print(f"    Pasos: error {fr['pasos_error']}")

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
    if recipe_details:
        counts = [d["ingredientes_de_despensa_usados"] for d in recipe_details]
        summary["ingredientes_de_despensa_por_receta_promedio"] = round(sum(counts) / len(counts), 1)
        summary["recetas_con_mas_de_6_ingredientes"] = sum(1 for c in counts if c > 6)
    summary["recetas_completas"] = full_recipes
    summary["detalle"] = rows
    summary["detalle_recetas"] = recipe_details
    if photos:
        # motivo: aprobada / vision_no / vision_fallo / sin_candidatas / sin_cupo / unsplash_bloqueado / repetida /
        # tiempo_agotado / sin_protagonistas (o error). Para revisar a mano si alguna foto es equivocada.
        summary["detalle_fotos"] = photo_details
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_recipes_result.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    print("\n=== RESUMEN ===")
    for k, v in summary.items():
        if k not in ("detalle", "detalle_fotos", "detalle_recetas"):
            print(f"{k}: {v}")
    print("\nRecetas (ingredientes de la despensa usados · dishNameEn):")
    for d in recipe_details:
        print(f"  {d['scenario']:<22} {d['ingredientes_de_despensa_usados']}/{d['ingredientes_de_despensa_total']}  {d['dishNameEn']}  <- {d['title']}")
    print("\nMetas: ingredientes inventados tras verificar = 0 · rechazo del verificador < 10 % · éxito >= 90 %")
    print(f"Resultado guardado en {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8, help="cuántas despensas probar (1-%d)" % len(SCENARIOS))
    ap.add_argument("--photos", action="store_true", help="buscar y verificar la foto de cada receta (Unsplash)")
    ap.add_argument("--solo", default=None, help="despensas a ejecutar, separadas por coma (ignora --n)")
    ap.add_argument("--incoherentes", action="store_true", help="atajo: las 3 despensas incoherentes")
    ap.add_argument("--pasos", action="store_true", help="genera también los pasos de cada receta entregada")
    args = ap.parse_args()
    by_name = {s[0]: s for s in SCENARIOS}
    if args.solo is not None or args.incoherentes:
        names = [x.strip() for x in (args.solo or "").split(",") if x.strip()]
        if args.incoherentes:
            names += [x for x in INCOHERENTES if x not in names]
        unknown = [x for x in names if x not in by_name]
        if unknown or not names:
            print(f"Despensa(s) desconocida(s): {', '.join(unknown) or '(vacío)'}. Válidas: {', '.join(by_name)}")
            raise SystemExit(2)
        selected = [by_name[x] for x in names]
    else:
        selected = SCENARIOS[:max(1, min(args.n, len(SCENARIOS)))]
    raise SystemExit(asyncio.run(main(selected, photos=args.photos, pasos=args.pasos)))
