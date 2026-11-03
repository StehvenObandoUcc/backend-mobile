"""Renovación de sesión (POST /api/v1/auth/refresh) y revocación por cambio de contraseña."""
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core import db
from app.core.config import settings
from app.core.security import create_jwt_token
from app.main import app

client = TestClient(app)

DEMO = {"sub": "usr-demo-1", "email": "demo@foodai.com"}
GRACE = settings.SESSION_REFRESH_GRACE_SECONDS


def _token(payload=DEMO, issued_ago=0, expires_in=3600):
    """Token emitido hace `issued_ago` segundos que dura `expires_in` desde su emisión."""
    real_now = time.time()
    with patch("app.core.security.time.time", return_value=real_now - issued_ago):
        return create_jwt_token(payload, expires_in=expires_in)


def _refresh(token):
    return client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {token}"})


def _me(token):
    return client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})


def test_refresh_valid_token_returns_new_working_token():
    res = _refresh(_token())
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["email"] == "demo@foodai.com"
    assert data["expires_at"]
    assert _me(data["access_token"]).status_code == 200


def test_refresh_recently_expired_token_within_grace():
    # Vencido hace 3 días: las rutas de datos lo rechazan, pero la renovación lo acepta.
    expired = _token(issued_ago=10 * 86400, expires_in=7 * 86400)
    assert _me(expired).status_code == 401
    res = _refresh(expired)
    assert res.status_code == 200
    assert _me(res.json()["access_token"]).status_code == 200


def test_refresh_token_expired_beyond_grace_is_rejected():
    too_old = _token(issued_ago=GRACE + 8 * 86400, expires_in=7 * 86400)
    res = _refresh(too_old)
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "session_expired"


def test_refresh_rejects_tampered_signature():
    token = _token()
    header, payload, sig = token.split(".")
    forged = f"{header}.{payload}.{'A' * len(sig)}"
    assert _refresh(forged).status_code == 401


def test_refresh_rejects_token_signed_with_other_secret():
    with patch("app.core.security.JWT_SECRET", "otro-secreto-que-no-es-el-del-servidor-1234567890"):
        foreign = create_jwt_token(DEMO)
    assert _refresh(foreign).status_code == 401


def test_refresh_rejects_unknown_or_deleted_user():
    assert _refresh(_token({"sub": "usr-no-existe", "email": "x@foodai.com"})).status_code == 401


def test_refresh_rejects_token_whose_email_no_longer_matches():
    assert _refresh(_token({"sub": "usr-demo-1", "email": "otro@foodai.com"})).status_code == 401


def test_refresh_requires_bearer_header():
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_revoked_sessions_are_rejected_everywhere_and_cannot_refresh():
    old = _token(issued_ago=60)
    db.revoke_user_sessions("usr-demo-1")
    assert _me(old).status_code == 401
    assert _refresh(old).status_code == 401
    # Una sesión nueva (emitida después de revocar) sí funciona.
    fresh = client.post("/api/v1/auth/login", json={"email": "demo@foodai.com", "password": "123456"})
    assert fresh.status_code == 200
    assert _me(fresh.json()["access_token"]).status_code == 200


def test_legacy_token_without_iat_still_works_until_revoked():
    # Tokens emitidos antes de este cambio no traen `iat`: valen mientras no se revoque nada.
    import base64, hashlib, hmac, json
    from app.core import security

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = b64(json.dumps({**DEMO, "exp": int(time.time()) + 3600}, separators=(",", ":")).encode())
    sig = hmac.new(security.JWT_SECRET.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    legacy = f"{header}.{body}.{b64(sig)}"
    assert _me(legacy).status_code == 200
    assert _refresh(legacy).status_code == 200
    db.revoke_user_sessions("usr-demo-1")
    assert _refresh(legacy).status_code == 401
