from datetime import date

from app.api.v1.scan import _resolve_expiration_with_source

TODAY = date(2026, 10, 24)


def test_fecha_impresa_es_label():
    assert _resolve_expiration_with_source({"expirationDate": "2026-11-15"}, TODAY) == ("2026-11-15", "label")


def test_vida_util_es_estimated():
    assert _resolve_expiration_with_source({"expirationDate": None, "shelfLifeDays": 7}, TODAY) == ("2026-10-31", "estimated")


def test_fecha_mal_formada_cae_en_estimada():
    assert _resolve_expiration_with_source({"expirationDate": "15/11/2026", "shelfLifeDays": 3}, TODAY) == ("2026-10-27", "estimated")


def test_sin_datos_es_unknown():
    assert _resolve_expiration_with_source({}, TODAY) == (None, "unknown")
