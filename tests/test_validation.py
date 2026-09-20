import pytest
from pydantic import ValidationError
from app.schemas.ingredient import IngredientItem
from app.schemas.auth import RegisterRequest, LoginRequest


def test_ingredient_item_valid():
    item = IngredientItem(
        id="ing-1",
        name="Tomates Frescos",
        category="vegetable",
        quantity=3.5,
        unit="kilograms",
        expirationDate="2026-10-15"
    )
    assert item.name == "Tomates Frescos"
    assert item.quantity == 3.5
    assert item.expirationDate == "2026-10-15"


def test_ingredient_item_rejects_negative_quantity():
    with pytest.raises(ValidationError):
        IngredientItem(
            id="ing-2",
            name="Zanahorias",
            quantity=-5
        )


def test_ingredient_item_rejects_excessive_quantity():
    with pytest.raises(ValidationError):
        IngredientItem(
            id="ing-3",
            name="Arroz",
            quantity=100000
        )


def test_ingredient_item_rejects_long_name():
    with pytest.raises(ValidationError):
        IngredientItem(
            id="ing-4",
            name="A" * 61
        )


def test_ingredient_item_rejects_blank_name():
    with pytest.raises(ValidationError):
        IngredientItem(
            id="ing-5",
            name="   "
        )


def test_ingredient_item_rejects_invalid_date_format():
    with pytest.raises(ValidationError):
        IngredientItem(
            id="ing-6",
            name="Leche",
            expirationDate="15/10/2026"
        )


def test_ingredient_item_rejects_out_of_range_year():
    with pytest.raises(ValidationError):
        IngredientItem(
            id="ing-7",
            name="Leche",
            expirationDate="2020-01-01"
        )
    with pytest.raises(ValidationError):
        IngredientItem(
            id="ing-8",
            name="Leche",
            expirationDate="2105-01-01"
        )


def test_auth_register_validations():
    # Valid
    req = RegisterRequest(
        email="test@foodai.com",
        password="secretpassword123",
        name="Chef Maria"
    )
    assert req.email == "test@foodai.com"
    assert req.name == "Chef Maria"

    # Invalid email
    with pytest.raises(ValidationError):
        RegisterRequest(email="correo-invalido", password="secretpassword123")

    # Short password (< 6)
    with pytest.raises(ValidationError):
        RegisterRequest(email="test@foodai.com", password="123")

    # Name too short (< 2)
    with pytest.raises(ValidationError):
        RegisterRequest(email="test@foodai.com", password="secretpassword123", name="A")

    # Name too long (> 50)
    with pytest.raises(ValidationError):
        RegisterRequest(email="test@foodai.com", password="secretpassword123", name="A" * 51)


def test_auth_login_validations():
    # Valid
    req = LoginRequest(email="demo@foodai.com", password="123456")
    assert req.email == "demo@foodai.com"

    # Invalid email
    with pytest.raises(ValidationError):
        LoginRequest(email="bademail", password="123456")

    # Short password
    with pytest.raises(ValidationError):
        LoginRequest(email="demo@foodai.com", password="123")
