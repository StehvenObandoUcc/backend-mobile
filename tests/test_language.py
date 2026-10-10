from starlette.requests import Request
from app.core.language import request_language, language_clause


def _req(value):
    headers = [] if value is None else [(b"accept-language", value.encode())]
    return Request({"type": "http", "headers": headers})


def test_request_language_defaults_to_spanish():
    assert request_language(_req(None)) == "es"
    assert request_language(_req("es-CO")) == "es"
    assert request_language(_req("fr")) == "es"


def test_request_language_english():
    assert request_language(_req("en")) == "en"
    assert request_language(_req("en-US,en;q=0.9")) == "en"


def test_language_clause_only_for_english():
    assert language_clause("es") == ""
    assert "INGLÉS" in language_clause("en")
