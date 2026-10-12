"""Contador de uso de DeepSeek en memoria (tokens y llamadas por tipo). Sirve para medir el costo por receta.

Cada respuesta de DeepSeek (recetas, pasos, visión) suma `prompt_tokens`, `completion_tokens` y una llamada a su
tipo, solo si trae `usage`; si no viene, no hace nada. Vive en la memoria del proceso (se reinicia con el dyno).
`record` nunca lanza: medir no puede romper la generación.
"""
import threading
from typing import Any, Dict

KINDS = ("recipes", "steps", "vision")

_lock = threading.Lock()
_usage: Dict[str, Dict[str, int]] = {}


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def record(kind: str, response_json: Any) -> None:
    """Suma el `usage` de una respuesta (dict ya decodificado) al tipo `kind`. Sin `usage` válido, no hace nada."""
    try:
        usage = response_json.get("usage") if isinstance(response_json, dict) else None
        if not isinstance(usage, dict):
            return
        with _lock:
            bucket = _usage.setdefault(str(kind), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
            bucket["calls"] += 1
            bucket["prompt_tokens"] += _count(usage.get("prompt_tokens"))
            bucket["completion_tokens"] += _count(usage.get("completion_tokens"))
    except Exception:  # noqa: BLE001
        pass


def snapshot() -> Dict[str, Dict[str, int]]:
    """Copia del contador: {tipo: {calls, prompt_tokens, completion_tokens}} para cada tipo (aunque esté en cero) y 'total'."""
    with _lock:
        out = {k: dict(_usage.get(k, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})) for k in KINDS}
        for k, v in _usage.items():
            out.setdefault(k, dict(v))
    out["total"] = {f: sum(v[f] for v in out.values()) for f in ("calls", "prompt_tokens", "completion_tokens")}
    return out


def reset() -> None:
    with _lock:
        _usage.clear()
