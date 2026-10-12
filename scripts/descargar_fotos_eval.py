"""Descarga en miniatura las fotos asignadas en una evaluación, para revisarlas a mano.

Uso (desde la carpeta backend, con el entorno virtual):
    python scripts/descargar_fotos_eval.py scripts/eval_recipes_result_antes.json scripts/eval_fotos/antes
    python scripts/descargar_fotos_eval.py scripts/eval_recipes_result.json scripts/eval_fotos/despues

Solo descarga imágenes públicas de images.unsplash.com (no usa claves ni cuenta en la cuota de la API).
Escribe además un indice.txt con escenario, título, plato, protagonistas, motivo y texto alt de cada foto.
"""
import json
import os
import re
import sys
from urllib.parse import urlparse

import httpx


def main(result_path: str, out_dir: str) -> int:
    with open(result_path, encoding="utf-8") as fh:
        data = json.load(fh)
    os.makedirs(out_dir, exist_ok=True)
    lines = []
    for i, item in enumerate(data.get("detalle_fotos", []), 1):
        uri = item.get("imageUri")
        name = f"{i:02d}-{re.sub(r'[^a-z0-9]+', '-', str(item.get('scenario', 'x')).lower())}"
        estado = "sin foto"
        if uri and urlparse(uri).hostname == "images.unsplash.com":
            small = re.sub(r"([?&])w=\d+", r"\1w=400", uri)
            try:
                resp = httpx.get(small, timeout=20, follow_redirects=True)
                resp.raise_for_status()
                with open(os.path.join(out_dir, name + ".jpg"), "wb") as img:
                    img.write(resp.content)
                estado = name + ".jpg"
            except Exception as exc:  # noqa: BLE001
                estado = f"error al descargar ({type(exc).__name__})"
        lines.append(
            f"{name} | {item.get('title')} | plato={item.get('dishNameEn')} | protagonistas={item.get('mainIngredientsEn')} "
            f"| motivo={item.get('motivo', '-')} | alt={item.get('altText')} | archivo={estado}"
        )
    with open(os.path.join(out_dir, "indice.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nListo: {out_dir}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
