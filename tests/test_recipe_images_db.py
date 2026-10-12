import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import db
from app.core.security import create_jwt_token

client = TestClient(app)

USER_ID = "usr-test-img-1"
USER_EMAIL = "imgtester@foodai.com"
AUTH = {"Authorization": f"Bearer {create_jwt_token({'sub': USER_ID, 'email': USER_EMAIL})}"}

PHOTO = "https://images.unsplash.com/photo-123?w=800"
CREDIT = {
    "source": "unsplash",
    "name": "Ana Fotógrafa",
    "profileUrl": "https://unsplash.com/@ana",
    "sourceUrl": "https://unsplash.com/photos/123",
}


@pytest.fixture(autouse=True)
def setup_user():
    if not db.get_user_by_id(USER_ID):
        db.create_user(USER_ID, USER_EMAIL, "Chef Fotos", "hash")


def _recipe(rec_id="rec-img-1", **extra):
    base = {
        "id": rec_id,
        "title": "Arroz con pollo",
        "description": "Clásico.",
        "prepTimeMinutes": 30,
        "servings": 2,
        "difficulty": "easy",
        "matchScore": 90,
        "steps": ["Cocinar."],
    }
    base.update(extra)
    return base


def test_guarda_y_lee_con_foto_y_credito():
    saved = db.save_recipe(_recipe(imageUri=PHOTO, imageCredit=CREDIT), USER_ID)
    assert saved["imageUri"] == PHOTO
    assert saved["imageCredit"] == CREDIT
    stored = db.get_saved_recipe_by_id("rec-img-1", USER_ID)
    assert stored["imageUri"] == PHOTO
    assert stored["imageCredit"] == CREDIT
    listed = db.get_saved_recipes_by_user(USER_ID)
    assert listed[0]["imageUri"] == PHOTO
    assert listed[0]["imageCredit"] == CREDIT


@pytest.mark.parametrize("bad", ["http://x.com/a.jpg", "javascript:alert(1)", "https://x.com/" + "a" * 2000, 123])
def test_url_no_https_o_demasiado_larga_se_descarta(bad):
    saved = db.save_recipe(_recipe(imageUri=bad, imageCredit=CREDIT), USER_ID)
    assert saved["imageUri"] is None
    assert db.get_saved_recipe_by_id("rec-img-1", USER_ID)["imageUri"] is None


def test_credito_con_claves_extra_se_recorta():
    credit = {**CREDIT, "extra": "x", "name": "N" * 301, "profileUrl": 5}
    db.save_recipe(_recipe(imageUri=PHOTO, imageCredit=credit), USER_ID)
    stored = db.get_saved_recipe_by_id("rec-img-1", USER_ID)["imageCredit"]
    assert stored == {"source": "unsplash", "sourceUrl": CREDIT["sourceUrl"]}


def test_credito_invalido_en_fila_devuelve_none():
    row = {"id": "x", "title": "t", "image_uri": PHOTO, "image_credit": "no es json"}
    assert db._row_to_recipe_dict(row)["imageCredit"] is None


def test_actualizar_sin_foto_conserva_la_existente():
    db.save_recipe(_recipe(imageUri=PHOTO, imageCredit=CREDIT), USER_ID)
    updated = db.save_recipe(_recipe(title="Arroz con pollo y verduras"), USER_ID)
    assert updated["imageUri"] == PHOTO
    stored = db.get_saved_recipe_by_id("rec-img-1", USER_ID)
    assert stored["title"] == "Arroz con pollo y verduras"
    assert stored["imageUri"] == PHOTO
    assert stored["imageCredit"] == CREDIT


def test_endpoint_save_ida_y_vuelta():
    res = client.post("/api/v1/recipes/save", json=_recipe(imageUri=PHOTO, imageCredit=CREDIT), headers=AUTH)
    assert res.status_code == 201
    assert res.json()["imageUri"] == PHOTO
    assert res.json()["imageCredit"] == CREDIT
    listed = client.get("/api/v1/recipes/saved", headers=AUTH).json()
    got = next(r for r in listed if r["id"] == "rec-img-1")
    assert got["imageUri"] == PHOTO
    assert got["imageCredit"] == CREDIT


class _Result:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, sb):
        self.sb = sb
        self.op = None
        self.payload = None

    def select(self, *_a):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, *_a):
        return self

    def execute(self):
        if self.op == "select":
            return _Result([])
        self.sb.writes.append(dict(self.payload))
        if len(self.sb.writes) == 1:
            raise Exception("Could not find the 'image_uri' column of 'recipes' in the schema cache")
        return _Result([dict(self.payload)])


class _FakeSupabase:
    def __init__(self):
        self.writes = []

    def table(self, _name):
        return _FakeTable(self)


def test_reintento_unico_sin_columnas_de_foto(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(db, "get_supabase", lambda: fake)
    saved = db.save_recipe(_recipe(imageUri=PHOTO, imageCredit=CREDIT), USER_ID)
    assert len(fake.writes) == 2
    assert "image_uri" in fake.writes[0] and "image_credit" in fake.writes[0]
    assert "image_uri" not in fake.writes[1] and "image_credit" not in fake.writes[1]
    assert saved["title"] == "Arroz con pollo"


def test_no_hay_tercer_intento(monkeypatch):
    class _AlwaysFail(_FakeTable):
        def execute(self):
            if self.op == "select":
                return _Result([])
            self.sb.writes.append(dict(self.payload))
            raise Exception("column image_credit does not exist")

    class _SB(_FakeSupabase):
        def table(self, _name):
            return _AlwaysFail(self)

    fake = _SB()
    monkeypatch.setattr(db, "get_supabase", lambda: fake)
    with pytest.raises(Exception):
        db.save_recipe(_recipe(imageUri=PHOTO, imageCredit=CREDIT), USER_ID)
    assert len(fake.writes) == 2


def test_credito_exige_https_en_urls():
    credit = {**CREDIT, "profileUrl": "http://unsplash.com/@ana", "sourceUrl": "javascript:x"}
    db.save_recipe(_recipe(imageUri=PHOTO, imageCredit=credit), USER_ID)
    stored = db.get_saved_recipe_by_id("rec-img-1", USER_ID)["imageCredit"]
    assert stored == {"source": "unsplash", "name": CREDIT["name"]}


def test_foto_sin_credito_valido_conserva_el_credito_existente():
    db.save_recipe(_recipe(imageUri=PHOTO, imageCredit=CREDIT), USER_ID)
    updated = db.save_recipe(_recipe(imageUri=PHOTO, imageCredit={"profileUrl": "http://x"}), USER_ID)
    assert updated["imageCredit"] == CREDIT
    assert db.get_saved_recipe_by_id("rec-img-1", USER_ID)["imageCredit"] == CREDIT


def test_init_db_agrega_solo_las_columnas_que_faltan():
    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(recipes);").fetchall()}
    assert {"image_uri", "image_credit"} <= cols
    db.init_db()  # idempotente: no falla si ya existen
