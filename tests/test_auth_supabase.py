"""Fase 4a: verificación de correo y «Olvidé mi contraseña» con Supabase Auth (sin red).

Un «GoTrue falso» en memoria reemplaza las funciones de app.services.supabase_auth.
"""
import asyncio
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core import db
from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.services import auth_service
from app.services import supabase_auth as sa
from app.services.auth_service import reset_auth_cooldowns

client = TestClient(app)

SIGNUP_CODE = "123456"
RECOVERY_CODE = "654321"


class FakeGoTrue:
    """Usuarios, códigos y estado confirmado en memoria; registra cada llamada."""

    def __init__(self):
        self.users = {}  # email -> {"id", "password", "confirmed"}
        self.codes = {}  # (email, tipo) -> código
        self.calls = []
        self.fail = {}  # nombre de función -> excepción a lanzar
        self._n = 0

    def _check(self, name, **info):
        self.calls.append((name, info))
        if name in self.fail:
            raise self.fail[name]

    def names(self):
        return [c[0] for c in self.calls]

    def _new(self, email, password, confirmed):
        self._n += 1
        self.users[email] = {"id": f"auth-{self._n}", "password": password, "confirmed": confirmed}
        return self.users[email]["id"]

    async def sign_up(self, email, password, name, http_client=None):
        self._check("sign_up", email=email)
        if email in self.users:
            raise sa.AlreadyRegistered("user_already_exists")
        if len(password) < 8:
            raise sa.WeakPassword("weak_password")
        auth_id = self._new(email, password, False)
        self.codes[(email, "signup")] = SIGNUP_CODE
        return auth_id

    async def sign_in(self, email, password, http_client=None):
        self._check("sign_in", email=email)
        user = self.users.get(email)
        if not user or user["password"] != password:
            raise sa.InvalidCredentials("invalid_credentials")
        if not user["confirmed"]:
            raise sa.EmailNotConfirmed("email_not_confirmed")
        return user["id"]

    async def verify_otp(self, email, token, type, http_client=None):
        self._check("verify_otp", email=email, type=type)
        user = self.users.get(email)
        if not user or self.codes.get((email, type)) != token:
            raise sa.InvalidOtp("otp_expired")
        user["confirmed"] = True
        return user["id"], f"at-{user['id']}"

    async def send_recovery(self, email, http_client=None):
        self._check("send_recovery", email=email)
        if email in self.users:
            self.codes[(email, "recovery")] = RECOVERY_CODE

    async def resend_signup(self, email, http_client=None):
        self._check("resend_signup", email=email)
        if email in self.users:
            self.codes[(email, "signup")] = SIGNUP_CODE

    async def update_password(self, access_token, new_password, http_client=None):
        self._check("update_password")
        user = next(u for u in self.users.values() if f"at-{u['id']}" == access_token)
        if user["password"] == new_password:
            raise sa.SamePassword("same_password")
        if len(new_password) < 8:
            raise sa.WeakPassword("weak_password")
        user["password"] = new_password

    async def admin_create_user(self, email, password, email_confirm, http_client=None):
        self._check("admin_create_user", email=email, email_confirm=email_confirm)
        if email in self.users:
            raise sa.AlreadyRegistered("email_exists")
        return self._new(email, password, email_confirm)

    async def admin_delete_user(self, auth_id, http_client=None):
        self._check("admin_delete_user", auth_id=auth_id)
        for email, user in list(self.users.items()):
            if user["id"] == auth_id:
                del self.users[email]


@pytest.fixture
def gotrue(monkeypatch):
    fake = FakeGoTrue()
    monkeypatch.setattr(settings, "AUTH_PROVIDER", "supabase")
    for name in (
        "sign_up", "sign_in", "verify_otp", "send_recovery", "resend_signup",
        "update_password", "admin_create_user", "admin_delete_user",
    ):
        monkeypatch.setattr(sa, name, getattr(fake, name))
    return fake


def _register(email, password="clave-larga-1", name="Prueba"):
    return client.post("/api/v1/auth/register", json={"email": email, "password": password, "name": name})


def _verify(email, password="clave-larga-1", code=SIGNUP_CODE):
    return client.post("/api/v1/auth/verify-email", json={"email": email, "code": code, "password": password})


def _login(email, password):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _registered_and_verified(email="chef@test.com", password="clave-larga-1"):
    assert _register(email, password).status_code == 202
    res = _verify(email, password)
    assert res.status_code == 200
    return res.json()


def _code(res):
    return res.json()["detail"]["code"]


def _old_account(email, password):
    """Cuenta del modo local: contraseña PBKDF2 propia, sin auth_id."""
    return db.create_user("usr-old00001", email, "Antiguo", hash_password(password))


# ─── Registro y verificación ───────────────────────────────────────────────────

def test_register_then_verify_email_gives_working_token(gotrue):
    res = _register("nuevo@test.com", name="Chef Ana")
    assert res.status_code == 202
    assert res.json() == {"status": "verification_required", "email": "nuevo@test.com"}
    user = db.get_user_by_email("nuevo@test.com")
    assert user["auth_id"] is None  # el id de sign_up no es fiable: se enlaza al verificar
    assert user["email_verified"] is False
    assert user["password_hash"] == "!supabase"

    ok = _verify("nuevo@test.com")
    assert ok.status_code == 200
    token = ok.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "nuevo@test.com"
    assert me.json()["name"] == "Chef Ana"
    verified = db.get_user_by_email("nuevo@test.com")
    assert verified["email_verified"] is True
    assert verified["auth_id"] == "auth-1"


def test_verify_email_wrong_code_is_400_invalid_code(gotrue):
    _register("codigo@test.com")
    res = _verify("codigo@test.com", code="000000")
    assert res.status_code == 400
    assert _code(res) == "invalid_code"
    assert db.get_user_by_email("codigo@test.com")["email_verified"] is False


def test_verify_email_rejects_malformed_code(gotrue):
    assert _verify("codigo@test.com", code="12ab56").status_code == 422
    assert _verify("codigo@test.com", code="12345").status_code == 422
    assert _verify("codigo@test.com", password="corta").status_code == 422


def test_register_short_password_is_weak_password_in_supabase_mode_without_calling_gotrue(gotrue):
    res = _register("corta@test.com", "1234567")
    assert res.status_code == 400
    assert _code(res) == "weak_password"
    assert gotrue.calls == []
    assert db.get_user_by_email("corta@test.com") is None


def test_register_local_mode_still_accepts_6_char_password():
    res = client.post("/api/v1/auth/register", json={"email": "seis@test.com", "password": "123456"})
    assert res.status_code == 201


def test_register_duplicate_verified_is_email_taken(gotrue):
    _registered_and_verified("dup@test.com")
    res = _register("dup@test.com")
    assert res.status_code == 400
    assert _code(res) == "email_taken"


def test_register_unverified_again_resends_code(gotrue):
    _register("pend@test.com")
    reset_auth_cooldowns()
    res = _register("pend@test.com")
    assert res.status_code == 202
    assert gotrue.names().count("resend_signup") == 1
    assert gotrue.names().count("sign_up") == 1


def test_register_existing_old_account_is_email_taken(gotrue):
    _old_account("viejo@test.com", "clave-vieja-1")
    res = _register("viejo@test.com")
    assert res.status_code == 400
    assert _code(res) == "email_taken"
    assert gotrue.calls == []


def test_register_pending_via_already_registered_is_202(gotrue):
    gotrue._new("pendiente@test.com", "otra-clave-1", False)
    res = _register("pendiente@test.com")
    assert res.status_code == 202
    assert gotrue.names() == ["sign_up", "resend_signup"]
    user = db.get_user_by_email("pendiente@test.com")
    assert user["password_hash"] == "!supabase"
    assert user["auth_id"] is None


def test_register_when_already_in_supabase_links_on_verify(gotrue):
    gotrue._new("huerfano@test.com", "otra-clave-1", False)
    gotrue.codes[("huerfano@test.com", "signup")] = SIGNUP_CODE
    res = _register("huerfano@test.com")
    assert res.status_code == 202
    assert "resend_signup" in gotrue.names()
    assert db.get_user_by_email("huerfano@test.com")["auth_id"] is None
    assert _verify("huerfano@test.com").status_code == 200
    user = db.get_user_by_email("huerfano@test.com")
    assert user["auth_id"] == "auth-1"
    assert user["email_verified"] is True


def test_verify_email_with_other_auth_id_is_conflict(gotrue):
    _register("conflicto@test.com")
    db.set_user_auth_link(db.get_user_by_email("conflicto@test.com")["id"], "auth-otro")
    res = _verify("conflicto@test.com")
    assert res.status_code == 409
    assert _code(res) == "account_conflict"


# ─── Login ─────────────────────────────────────────────────────────────────────

def test_login_before_verifying_is_403_and_resends_code(gotrue):
    _register("sinverificar@test.com")
    reset_auth_cooldowns()
    res = _login("sinverificar@test.com", "clave-larga-1")
    assert res.status_code == 403
    assert _code(res) == "email_not_verified"
    assert gotrue.names().count("resend_signup") == 1


def test_login_before_verifying_ignores_resend_rate_limit(gotrue):
    _register("limite@test.com")
    reset_auth_cooldowns()
    gotrue.fail["resend_signup"] = sa.RateLimited("over_email_send_rate_limit")
    res = _login("limite@test.com", "clave-larga-1")
    assert res.status_code == 403
    assert _code(res) == "email_not_verified"


def test_login_verified_user_ok(gotrue):
    _registered_and_verified("ok@test.com")
    res = _login("ok@test.com", "clave-larga-1")
    assert res.status_code == 200
    assert res.json()["user"]["email"] == "ok@test.com"


def test_login_bad_credentials_is_401(gotrue):
    _registered_and_verified("mala@test.com")
    bad = _login("mala@test.com", "clave-errada-1")
    assert bad.status_code == 401
    assert _code(bad) == "invalid_credentials"
    unknown = _login("nadie@test.com", "clave-larga-1")
    assert unknown.status_code == 401
    assert _code(unknown) == "invalid_credentials"


def test_login_old_account_correct_password_migrates_and_requires_verification(gotrue):
    _old_account("viejo@test.com", "clave-vieja-1")
    res = _login("viejo@test.com", "clave-vieja-1")
    assert res.status_code == 403
    assert _code(res) == "email_not_verified"
    user = db.get_user_by_email("viejo@test.com")
    assert user["auth_id"] is None  # el id de sign_up no es fiable
    assert user["password_hash"] == "!supabase"
    assert user["email_verified"] is False
    # Con el código ya puede entrar
    assert _verify("viejo@test.com", "clave-vieja-1").status_code == 200
    assert _login("viejo@test.com", "clave-vieja-1").status_code == 200


def test_login_old_account_weak_password_requires_reset(gotrue):
    _old_account("debil@test.com", "corta1")
    res = _login("debil@test.com", "corta1")
    assert res.status_code == 403
    assert _code(res) == "password_reset_required"
    assert gotrue.names() == ["sign_up", "admin_create_user", "send_recovery"]
    assert gotrue.calls[1][1]["email_confirm"] is True
    user = db.get_user_by_email("debil@test.com")
    assert user["auth_id"] == "auth-1"  # el id de admin_create_user sí es fiable
    assert user["password_hash"] == "!supabase"
    assert user["email_verified"] is False
    # Termina con el flujo de recuperación
    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"email": "debil@test.com", "code": RECOVERY_CODE, "new_password": "clave-nueva-1"},
    )
    assert reset.status_code == 200
    assert db.get_user_by_email("debil@test.com")["email_verified"] is True


def test_login_old_account_wrong_password_never_calls_gotrue(gotrue):
    _old_account("viejo2@test.com", "clave-vieja-1")
    res = _login("viejo2@test.com", "clave-errada-1")
    assert res.status_code == 401
    assert _code(res) == "invalid_credentials"
    assert gotrue.calls == []


def test_supabase_marker_is_never_a_valid_password_hash():
    assert verify_password("!supabase", "!supabase") is False
    assert verify_password("", "!supabase") is False
    assert verify_password("cualquiera", "!supabase") is False


# ─── Seguridad: toma de cuenta previa a la verificación ────────────────────────

def test_attacker_cannot_keep_password_chosen_at_registration(gotrue):
    """El atacante registra el correo de la víctima con SU clave X; la víctima verifica con la suya Y."""
    assert _register("victima@test.com", "clave-atacante-X").status_code == 202
    assert _verify("victima@test.com", "clave-victima-Y").status_code == 200
    assert _login("victima@test.com", "clave-atacante-X").status_code == 401
    assert _login("victima@test.com", "clave-victima-Y").status_code == 200


def test_verify_with_same_password_as_registration_is_fine(gotrue):
    _register("igual@test.com", "clave-larga-1")
    assert _verify("igual@test.com", "clave-larga-1").status_code == 200


@pytest.mark.parametrize("failure", [sa.AuthUnavailable("timeout"), sa.WeakPassword("weak_password"), sa.RateLimited("x")])
def test_verify_update_password_failure_discards_account_and_allows_new_signup(gotrue, failure):
    _register("falla@test.com", "clave-atacante-X")
    gotrue.fail["update_password"] = failure
    res = _verify("falla@test.com", "clave-victima-Y")
    assert res.status_code == 503
    assert res.json()["detail"] == {
        "code": "auth_unavailable",
        "message": "No pudimos terminar la verificación. Regístrate de nuevo.",
    }
    assert "access_token" not in res.text
    assert ("admin_delete_user", {"auth_id": "auth-1"}) in gotrue.calls
    assert "falla@test.com" not in gotrue.users
    assert db.get_user_by_email("falla@test.com") is None

    gotrue.fail.clear()
    assert _register("falla@test.com", "clave-victima-Y").status_code == 202
    assert _verify("falla@test.com", "clave-victima-Y").status_code == 200
    assert _login("falla@test.com", "clave-atacante-X").status_code == 401
    assert _login("falla@test.com", "clave-victima-Y").status_code == 200


def test_verify_update_password_failure_with_failed_cleanup_still_recovers(gotrue):
    _register("falla2@test.com", "clave-atacante-X")
    gotrue.fail["update_password"] = sa.AuthUnavailable("timeout")
    gotrue.fail["admin_delete_user"] = sa.AuthUnavailable("timeout")
    assert _verify("falla2@test.com", "clave-victima-Y").status_code == 503
    assert "falla2@test.com" in gotrue.users  # el borrado remoto falló
    gotrue.fail.clear()
    # GoTrue dice AlreadyRegistered: se trata como pendiente (reenvío) y 202
    assert _register("falla2@test.com", "clave-victima-Y").status_code == 202
    assert _verify("falla2@test.com", "clave-victima-Y").status_code == 200
    assert _login("falla2@test.com", "clave-victima-Y").status_code == 200


def test_verify_conflict_is_checked_before_changing_password(gotrue):
    _register("c1@test.com")
    db.set_user_auth_link(db.get_user_by_email("c1@test.com")["id"], "auth-otro")
    gotrue.calls.clear()
    assert _verify("c1@test.com").status_code == 409
    assert "update_password" not in gotrue.names()


# ─── Reenvío de código y cooldown ──────────────────────────────────────────────

def test_resend_code_is_generic_and_only_sends_for_unverified(gotrue):
    _register("reenvio@test.com")
    reset_auth_cooldowns()
    gotrue.calls.clear()
    res = client.post("/api/v1/auth/resend-code", json={"email": "reenvio@test.com"})
    assert res.status_code == 200
    assert gotrue.names() == ["resend_signup"]

    gotrue.calls.clear()
    unknown = client.post("/api/v1/auth/resend-code", json={"email": "nadie@test.com"})
    assert unknown.status_code == 200
    assert unknown.json() == res.json()
    assert gotrue.calls == []


def test_resend_code_for_old_account_or_verified_account_does_not_call_gotrue(gotrue):
    _old_account("viejo@test.com", "clave-vieja-1")
    _registered_and_verified("listo@test.com")
    gotrue.calls.clear()
    for email in ("viejo@test.com", "listo@test.com"):
        res = client.post("/api/v1/auth/resend-code", json={"email": email})
        assert res.status_code == 200
    assert gotrue.calls == []


def test_cooldown_prevents_second_send_within_a_minute(gotrue):
    _register("enfriar@test.com")  # sign_up ya envió el código: queda en cooldown
    gotrue.calls.clear()
    for _ in range(2):
        assert client.post("/api/v1/auth/resend-code", json={"email": "enfriar@test.com"}).status_code == 200
    assert _login("enfriar@test.com", "clave-larga-1").status_code == 403
    assert gotrue.names() == ["sign_in"]  # ningún reenvío dentro del cooldown

    reset_auth_cooldowns()
    client.post("/api/v1/auth/resend-code", json={"email": "enfriar@test.com"})
    client.post("/api/v1/auth/resend-code", json={"email": "enfriar@test.com"})
    assert gotrue.names().count("resend_signup") == 1


def test_cooldown_expires_after_60_seconds(gotrue, monkeypatch):
    _register("expira@test.com")
    gotrue.calls.clear()
    real = auth_service.time.monotonic
    monkeypatch.setattr(auth_service.time, "monotonic", lambda: real() + 61)
    client.post("/api/v1/auth/resend-code", json={"email": "expira@test.com"})
    assert gotrue.names() == ["resend_signup"]


@pytest.mark.parametrize("failure", [sa.RateLimited("over_email_send_rate_limit"), sa.AuthUnavailable("timeout")])
def test_resend_and_forgot_look_identical_for_unknown_and_existing_emails(gotrue, failure):
    _registered_and_verified("existe2@test.com")
    _register("pendiente2@test.com")
    reset_auth_cooldowns()
    gotrue.fail["resend_signup"] = failure
    gotrue.fail["send_recovery"] = failure
    results = []
    for path, email in (
        ("resend-code", "pendiente2@test.com"),
        ("resend-code", "noexiste2@test.com"),
        ("forgot-password", "existe2@test.com"),
        ("forgot-password", "noexiste2@test.com"),
    ):
        res = client.post(f"/api/v1/auth/{path}", json={"email": email})
        results.append((path, res.status_code, res.json()))
        reset_auth_cooldowns()
    assert [r[1] for r in results] == [200, 200, 200, 200]
    assert results[0][2] == results[1][2]
    assert results[2][2] == results[3][2]


def test_auth_unavailable_is_503_in_register_and_login(gotrue):
    gotrue.fail["sign_up"] = sa.AuthUnavailable("network_error")
    assert _register("otro@test.com").status_code == 503
    gotrue.fail.clear()
    _register("caido@test.com")
    reset_auth_cooldowns()
    gotrue.fail["resend_signup"] = sa.AuthUnavailable("timeout")
    res = _login("caido@test.com", "clave-larga-1")
    assert res.status_code == 503
    assert _code(res) == "auth_unavailable"


def test_login_sign_in_with_different_auth_id_is_conflict(gotrue):
    _registered_and_verified("id@test.com")
    # El id de Supabase deja de coincidir con el guardado
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET auth_id = 'auth-distinto' WHERE email = 'id@test.com'")
        conn.commit()
    res = _login("id@test.com", "clave-larga-1")
    assert res.status_code == 409
    assert _code(res) == "account_conflict"


# ─── Olvidé mi contraseña ──────────────────────────────────────────────────────

def test_forgot_password_unknown_email_is_generic_without_sending(gotrue):
    _registered_and_verified("existe@test.com")
    gotrue.calls.clear()
    known = client.post("/api/v1/auth/forgot-password", json={"email": "existe@test.com"})
    assert gotrue.names() == ["send_recovery"]
    gotrue.calls.clear()
    unknown = client.post("/api/v1/auth/forgot-password", json={"email": "noexiste@test.com"})
    assert unknown.status_code == 200
    assert unknown.json() == known.json()
    assert gotrue.calls == []


def test_forgot_and_reset_password_gives_token_and_new_login(gotrue):
    _registered_and_verified("olvido@test.com", "clave-larga-1")
    assert client.post("/api/v1/auth/forgot-password", json={"email": "olvido@test.com"}).status_code == 200
    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"email": "olvido@test.com", "code": RECOVERY_CODE, "new_password": "clave-nueva-1"},
    )
    assert reset.status_code == 200
    token = reset.json()["access_token"]
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert _login("olvido@test.com", "clave-nueva-1").status_code == 200
    assert _login("olvido@test.com", "clave-larga-1").status_code == 401


def test_forgot_password_old_account_keeps_old_password_until_reset(gotrue):
    _old_account("antiguo@test.com", "clave-vieja-1")
    res = client.post("/api/v1/auth/forgot-password", json={"email": "antiguo@test.com"})
    assert res.status_code == 200
    assert gotrue.names() == ["admin_create_user", "send_recovery"]
    assert gotrue.calls[0][1]["email_confirm"] is True
    row = db.get_user_by_email("antiguo@test.com")
    assert row["auth_id"] is None
    assert verify_password("clave-vieja-1", row["password_hash"]) is True  # la antigua sigue sirviendo

    reset = client.post(
        "/api/v1/auth/reset-password",
        json={"email": "antiguo@test.com", "code": RECOVERY_CODE, "new_password": "clave-nueva-1"},
    )
    assert reset.status_code == 200
    row = db.get_user_by_email("antiguo@test.com")
    assert row["auth_id"] == "auth-1"
    assert row["password_hash"] == "!supabase"
    assert row["email_verified"] is True
    assert _login("antiguo@test.com", "clave-nueva-1").status_code == 200
    assert _login("antiguo@test.com", "clave-vieja-1").status_code == 401


def test_forgot_password_old_account_already_in_supabase_still_sends_recovery(gotrue):
    _old_account("antiguo@test.com", "clave-vieja-1")
    gotrue._new("antiguo@test.com", "otra-clave-1", True)
    res = client.post("/api/v1/auth/forgot-password", json={"email": "antiguo@test.com"})
    assert res.status_code == 200
    assert gotrue.names() == ["admin_create_user", "send_recovery"]
    assert db.get_user_by_email("antiguo@test.com")["auth_id"] is None


def test_reset_password_bad_code_is_400(gotrue):
    _registered_and_verified("reset@test.com")
    client.post("/api/v1/auth/forgot-password", json={"email": "reset@test.com"})
    res = client.post(
        "/api/v1/auth/reset-password",
        json={"email": "reset@test.com", "code": "000000", "new_password": "clave-nueva-1"},
    )
    assert res.status_code == 400
    assert _code(res) == "invalid_code"
    assert _login("reset@test.com", "clave-larga-1").status_code == 200


def test_reset_password_same_and_weak_password_codes(gotrue):
    _registered_and_verified("reset2@test.com", "clave-larga-1")
    client.post("/api/v1/auth/forgot-password", json={"email": "reset2@test.com"})
    body = {"email": "reset2@test.com", "code": RECOVERY_CODE}
    same = client.post("/api/v1/auth/reset-password", json={**body, "new_password": "clave-larga-1"})
    assert same.status_code == 400
    assert _code(same) == "same_password"
    gotrue.fail["update_password"] = sa.WeakPassword("weak_password")
    weak = client.post("/api/v1/auth/reset-password", json={**body, "new_password": "clave-nueva-1"})
    assert weak.status_code == 400
    assert _code(weak) == "weak_password"


def test_reset_password_conflict_is_checked_before_update_password(gotrue):
    _registered_and_verified("reset3@test.com")
    client.post("/api/v1/auth/forgot-password", json={"email": "reset3@test.com"})
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET auth_id = 'auth-distinto' WHERE email = 'reset3@test.com'")
        conn.commit()
    gotrue.calls.clear()
    res = client.post(
        "/api/v1/auth/reset-password",
        json={"email": "reset3@test.com", "code": RECOVERY_CODE, "new_password": "clave-nueva-1"},
    )
    assert res.status_code == 409
    assert "update_password" not in gotrue.names()


def test_reset_password_requires_min_8_chars():
    res = client.post(
        "/api/v1/auth/reset-password",
        json={"email": "a@test.com", "code": "123456", "new_password": "1234567"},
    )
    assert res.status_code == 422


# ─── Borrado de cuenta ─────────────────────────────────────────────────────────

def test_delete_account_supabase_removes_remote_then_local_user(gotrue):
    data = _registered_and_verified("borrar@test.com")
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    wrong = client.post("/api/v1/auth/delete-account", json={"password": "clave-errada-1"}, headers=headers)
    assert wrong.status_code == 401
    assert "admin_delete_user" not in gotrue.names()

    res = client.post("/api/v1/auth/delete-account", json={"password": "clave-larga-1"}, headers=headers)
    assert res.status_code == 204
    assert ("admin_delete_user", {"auth_id": "auth-1"}) in gotrue.calls
    assert db.get_user_by_email("borrar@test.com") is None
    assert "borrar@test.com" not in gotrue.users


def test_delete_account_admin_failure_is_503_and_keeps_everything(gotrue):
    data = _registered_and_verified("borrar2@test.com")
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    client.post("/api/v1/inventory", json={"name": "Tomate", "quantity": 2, "unit": "unit", "category": "vegetable"}, headers=headers)
    gotrue.fail["admin_delete_user"] = sa.AuthUnavailable("timeout")
    res = client.post("/api/v1/auth/delete-account", json={"password": "clave-larga-1"}, headers=headers)
    assert res.status_code == 503
    assert _code(res) == "auth_unavailable"
    assert db.get_user_by_email("borrar2@test.com") is not None
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert len(client.get("/api/v1/inventory", headers=headers).json()) == 1
    assert "borrar2@test.com" in gotrue.users
    # Reintento posterior con Supabase de vuelta: ahora sí se borra
    gotrue.fail.clear()
    assert client.post("/api/v1/auth/delete-account", json={"password": "clave-larga-1"}, headers=headers).status_code == 204


def test_delete_account_uses_sign_in_id_when_row_has_no_auth_id(gotrue):
    data = _registered_and_verified("borrar3@test.com")
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET auth_id = NULL WHERE email = 'borrar3@test.com'")
        conn.commit()
    assert client.post("/api/v1/auth/delete-account", json={"password": "clave-larga-1"}, headers=headers).status_code == 204
    assert ("admin_delete_user", {"auth_id": "auth-1"}) in gotrue.calls


def test_token_of_deleted_account_does_not_enter_new_account_with_same_email(gotrue):
    old = _registered_and_verified("reuso@test.com")
    old_headers = {"Authorization": f"Bearer {old['access_token']}"}
    assert client.post(
        "/api/v1/auth/delete-account", json={"password": "clave-larga-1"}, headers=old_headers
    ).status_code == 204
    new = _registered_and_verified("reuso@test.com", "otra-clave-9")
    assert new["user"]["id"] != old["user"]["id"]
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 401
    new_headers = {"Authorization": f"Bearer {new['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=new_headers).status_code == 200


# ─── Errores de base de datos ──────────────────────────────────────────────────

def test_database_failure_in_supabase_flow_is_503_with_migration_hint(gotrue, monkeypatch, caplog):
    def broken(**_kwargs):
        raise sqlite3.OperationalError("table users has no column named auth_id")

    monkeypatch.setattr(auth_service, "create_user", broken)
    res = _register("bd@test.com")
    assert res.status_code == 503
    assert _code(res) == "auth_unavailable"
    assert "03_supabase_auth.sql" in caplog.text


def test_duplicate_auth_id_integrity_error_is_409(gotrue, monkeypatch):
    def duplicated(**_kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: users.auth_id")

    monkeypatch.setattr(auth_service, "create_user", duplicated)
    res = _register("dupid@test.com")
    assert res.status_code == 409
    assert _code(res) == "account_conflict"


# ─── Validación 422 ────────────────────────────────────────────────────────────

def test_validation_error_does_not_echo_the_submitted_password():
    res = client.post("/api/v1/auth/register", json={"email": "a@test.com", "password": "pwd-5"})
    assert res.status_code == 422
    assert "pwd-5" not in res.text
    detail = res.json()["detail"]
    assert detail and all("input" not in err for err in detail)
    assert detail[0]["loc"][-1] == "password"
    assert "msg" in detail[0] and "type" in detail[0]


# ─── Modo local ────────────────────────────────────────────────────────────────

def test_local_mode_new_endpoints_are_404_not_available():
    assert settings.AUTH_PROVIDER == "local"
    for path, body in (
        ("/api/v1/auth/verify-email", {"email": "a@test.com", "code": "123456", "password": "clave-larga-1"}),
        ("/api/v1/auth/forgot-password", {"email": "a@test.com"}),
        ("/api/v1/auth/resend-code", {"email": "a@test.com"}),
        ("/api/v1/auth/reset-password", {"email": "a@test.com", "code": "123456", "new_password": "clave-nueva-1"}),
    ):
        res = client.post(path, json=body)
        assert res.status_code == 404, path
        assert res.json()["detail"]["code"] == "not_available"


def test_local_mode_register_creates_verified_user():
    res = _register("local@test.com")
    assert res.status_code == 201
    user = db.get_user_by_email("local@test.com")
    assert user["email_verified"] is True
    assert user["auth_id"] is None


def test_jwt_email_claim_no_longer_falls_back_to_email_lookup():
    """Un token cuyo `sub` ya no existe no entra a otra cuenta con el mismo correo."""
    from app.core.security import create_jwt_token
    token = create_jwt_token({"sub": "usr-inexistente", "email": "demo@foodai.com"})
    res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


# ─── Configuración ─────────────────────────────────────────────────────────────

def test_auth_provider_invalid_value_falls_back_to_local(monkeypatch):
    from app.core.config import _resolve_auth_provider
    monkeypatch.setenv("AUTH_PROVIDER", "  Supabase ")
    assert _resolve_auth_provider() == "supabase"
    monkeypatch.setenv("AUTH_PROVIDER", "firebase")
    assert _resolve_auth_provider() == "local"
    monkeypatch.delenv("AUTH_PROVIDER")
    assert _resolve_auth_provider() == "local"


def test_invalid_auth_provider_in_production_refuses_to_start(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_PROVIDER_INVALID", True)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "JWT_SECRET", "x" * 40)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_invalid_timeout_env_falls_back_to_default(monkeypatch, caplog):
    from app.core.config import _float_env
    monkeypatch.setenv("SUPABASE_AUTH_TIMEOUT_SECONDS", "abc")
    assert _float_env("SUPABASE_AUTH_TIMEOUT_SECONDS", 10.0) == 10.0
    assert "SUPABASE_AUTH_TIMEOUT_SECONDS" in caplog.text
    monkeypatch.setenv("SUPABASE_AUTH_TIMEOUT_SECONDS", "2.5")
    assert _float_env("SUPABASE_AUTH_TIMEOUT_SECONDS", 10.0) == 2.5


def test_production_refuses_supabase_auth_without_credentials(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_PROVIDER", "supabase")
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "JWT_SECRET", "x" * 40)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


# ─── Cliente GoTrue: mapeo de errores con respuestas falsas de httpx ───────────

@pytest.fixture
def gotrue_url(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proyecto.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_KEY", "service-role-key")


def _call(coro_fn, handler):
    """Ejecuta una función de supabase_auth contra un transporte falso; devuelve (resultado|excepción, nº de llamadas)."""
    hits = []

    def counting(request):
        hits.append(request)
        return handler(request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(counting)) as http:
            try:
                return await coro_fn(http)
            except sa.SupabaseAuthError as err:
                return err

    return asyncio.run(run()), hits


@pytest.mark.parametrize(
    "status_code,body,expected",
    [
        (400, {"error_code": "invalid_credentials", "msg": "Invalid login credentials"}, sa.InvalidCredentials),
        (400, {"error": "invalid_grant", "error_description": "Invalid login credentials"}, sa.InvalidCredentials),
        (400, {"error_code": "email_not_confirmed", "msg": "Email not confirmed"}, sa.EmailNotConfirmed),
        (400, {"error": "invalid_grant", "error_description": "Email not confirmed"}, sa.EmailNotConfirmed),
        (422, {"error_code": "weak_password", "msg": "Password should be at least 8 characters"}, sa.WeakPassword),
        (422, {"msg": "Password should be at least 6 characters"}, sa.WeakPassword),
        (422, {"error_code": "user_already_exists", "msg": "User already registered"}, sa.AlreadyRegistered),
        (422, {"msg": "A user with this email address has already been registered"}, sa.AlreadyRegistered),
        (429, {"error_code": "over_email_send_rate_limit", "msg": "rate limit"}, sa.RateLimited),
        (400, {"error_code": "over_request_rate_limit", "msg": "rate limit"}, sa.RateLimited),
        (500, {"msg": "boom"}, sa.AuthUnavailable),
        (503, {"error": "unavailable"}, sa.AuthUnavailable),
    ],
)
def test_error_mapping_for_sign_in(gotrue_url, status_code, body, expected):
    result, hits = _call(
        lambda http: sa.sign_in("a@test.com", "clave-larga-1", http_client=http),
        lambda req: httpx.Response(status_code, json=body),
    )
    assert type(result) is expected
    assert len(hits) == 1  # sin reintentos


@pytest.mark.parametrize(
    "status_code,body",
    [
        (403, {"error_code": "otp_expired", "msg": "Token has expired or is invalid"}),
        (403, {"msg": "whatever"}),
        (400, {"msg": "Token has expired or is invalid"}),
        (400, {"error": "invalid", "error_description": "Invalid OTP"}),
    ],
)
def test_error_mapping_for_verify_is_invalid_otp(gotrue_url, status_code, body):
    result, hits = _call(
        lambda http: sa.verify_otp("a@test.com", "123456", "signup", http_client=http),
        lambda req: httpx.Response(status_code, json=body),
    )
    assert type(result) is sa.InvalidOtp
    assert len(hits) == 1


def test_error_mapping_same_password_and_unknown_code(gotrue_url):
    result, _ = _call(
        lambda http: sa.update_password("tok", "clave-larga-1", http_client=http),
        lambda req: httpx.Response(422, json={"error_code": "same_password", "msg": "x"}),
    )
    assert type(result) is sa.SamePassword
    result, _ = _call(
        lambda http: sa.send_recovery("a@test.com", http_client=http),
        lambda req: httpx.Response(400, json={"error_code": "raro", "msg": "x"}),
    )
    assert type(result) is sa.SupabaseAuthError
    assert result.code == "raro"


def test_timeout_network_error_and_non_json_are_auth_unavailable_without_retries(gotrue_url):
    def timeout(request):
        raise httpx.ReadTimeout("lento", request=request)

    def network(request):
        raise httpx.ConnectError("sin red", request=request)

    for handler in (timeout, network, lambda req: httpx.Response(200, text="<html>no es json</html>")):
        result, hits = _call(lambda http: sa.resend_signup("a@test.com", http_client=http), handler)
        assert type(result) is sa.AuthUnavailable
        assert len(hits) == 1


def test_requests_are_built_as_specified(gotrue_url):
    seen = {}

    def handler(request):
        seen[request.url.path + "?" + request.url.query.decode()] = request
        if request.url.path.endswith("/verify"):
            return httpx.Response(200, json={"access_token": "tok-1", "user": {"id": "auth-9"}})
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": "t", "user": {"id": "auth-3"}})
        return httpx.Response(200, json={"id": "auth-7"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            assert await sa.sign_up("a@test.com", "clave-larga-1", "Ana", http_client=http) == "auth-7"
            assert await sa.sign_in("a@test.com", "clave-larga-1", http_client=http) == "auth-3"
            assert await sa.verify_otp("a@test.com", "123456", "recovery", http_client=http) == ("auth-9", "tok-1")
            await sa.update_password("tok-1", "clave-nueva-1", http_client=http)
            await sa.admin_delete_user("auth-7", http_client=http)

    asyncio.run(run())
    signup = seen["/auth/v1/signup?"]
    assert json.loads(signup.content) == {"email": "a@test.com", "password": "clave-larga-1", "data": {"name": "Ana"}}
    assert signup.headers["apikey"] == "service-role-key"
    assert signup.headers["authorization"] == "Bearer service-role-key"
    assert "/auth/v1/token?grant_type=password" in seen
    assert json.loads(seen["/auth/v1/verify?"].content) == {"type": "recovery", "email": "a@test.com", "token": "123456"}
    put = seen["/auth/v1/user?"]
    assert put.method == "PUT"
    assert put.headers["authorization"] == "Bearer tok-1"
    assert put.headers["apikey"] == "service-role-key"
    assert json.loads(put.content) == {"password": "clave-nueva-1"}
    assert seen["/auth/v1/admin/users/auth-7?"].method == "DELETE"


def test_anon_key_used_for_user_calls_and_service_key_for_admin(gotrue_url, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "anon-key")
    seen = {}

    def handler(request):
        seen[request.url.path] = request
        return httpx.Response(200, json={"id": "auth-1", "access_token": "tok", "user": {"id": "auth-1"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await sa.sign_up("a@test.com", "clave-larga-1", "Ana", http_client=http)
            await sa.verify_otp("a@test.com", "123456", "signup", http_client=http)
            await sa.send_recovery("a@test.com", http_client=http)
            await sa.resend_signup("a@test.com", http_client=http)
            await sa.sign_in("a@test.com", "clave-larga-1", http_client=http)
            await sa.update_password("tok", "clave-nueva-1", http_client=http)
            await sa.admin_create_user("a@test.com", "x" * 32, True, http_client=http)
            await sa.admin_delete_user("auth-1", http_client=http)

    asyncio.run(run())
    for path in ("signup", "verify", "recover", "resend", "token", "user"):
        assert seen[f"/auth/v1/{path}"].headers["apikey"] == "anon-key", path
    assert seen["/auth/v1/user"].headers["authorization"] == "Bearer tok"
    for path in ("/auth/v1/admin/users", "/auth/v1/admin/users/auth-1"):
        assert seen[path].headers["apikey"] == "service-role-key", path
        assert seen[path].headers["authorization"] == "Bearer service-role-key"


def test_admin_delete_escapes_id_and_treats_404_as_done(gotrue_url):
    seen = []

    def handler(request):
        seen.append(request.url.raw_path.decode())
        return httpx.Response(404, json={"error_code": "user_not_found", "msg": "User not found"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await sa.admin_delete_user("../x?y=1/z", http_client=http)

    asyncio.run(run())
    assert seen == ["/auth/v1/admin/users/..%2Fx%3Fy%3D1%2Fz"]


def test_secrets_are_never_logged(gotrue_url, caplog):
    caplog.set_level("DEBUG")
    _call(
        lambda http: sa.verify_otp("a@test.com", "987654", "signup", http_client=http),
        lambda req: httpx.Response(403, json={"error_code": "otp_expired", "msg": "Token has expired or is invalid"}),
    )
    _call(
        lambda http: sa.sign_in("a@test.com", "super-secreta-1", http_client=http),
        lambda req: httpx.Response(400, json={"error_code": "invalid_credentials", "msg": "Invalid login credentials"}),
    )
    logs = caplog.text
    for secret in ("987654", "super-secreta-1", "service-role-key"):
        assert secret not in logs
