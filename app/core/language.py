"""Idioma de las respuestas de IA según la app (encabezado Accept-Language: 'es' o 'en')."""
from fastapi import Request


def request_language(request: Request) -> str:
    """Devuelve 'en' si la app pidió inglés; en cualquier otro caso 'es' (predeterminado)."""
    value = (request.headers.get("accept-language") or "").strip().lower()
    return "en" if value.startswith("en") else "es"


def language_clause(language: str) -> str:
    """Instrucción extra para el prompt. En español no agrega nada (los prompts ya están en español)."""
    if language != "en":
        return ""
    return (
        "\n\nIDIOMA DE RESPUESTA: INGLÉS. Escribe en inglés natural todos los textos que verá el usuario "
        "(title, description, name de cada ingrediente, substitutions, warnings y cada paso). "
        "Las claves del JSON y los valores de unit, category y difficulty NO se traducen. "
        "No antepongas 'Step N:' a los pasos."
    )
