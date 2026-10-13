import time
import uuid

import jwt
import pytest
from fastapi import HTTPException

from app.crypto import encrypt
from app.models import ChatApp, User
from app.routers.embed import EmbedSettings, IdentityClaims, frame_ancestors, preview_identity, secret_context, verify_identity

SECRET = "s" * 40


def _app(**overrides) -> ChatApp:
    app_id = uuid.uuid4()
    values = dict(id=app_id, name="Catalog", embed_enabled=True, embed_auth="signed",
                  allowed_domains=["https://erp.example.com"], encrypted_embed_secret=encrypt(SECRET, secret_context(app_id)))
    values.update(overrides)
    return ChatApp(**values)


def _token(app, secret=SECRET, lifetime=3600, **claims):
    now = int(time.time())
    body = {"sub": "jdoe", "email": "jdoe@example.com", "name": "Jane", "aud": str(app.id), "iat": now, "exp": now + lifetime}
    body.update(claims)
    body = {k: v for k, v in body.items() if v is not None}
    return jwt.encode(body, secret, algorithm="HS256")


def test_valid_token():
    app = _app()
    claims = verify_identity(app, _token(app))
    assert (claims.sub, claims.email, claims.name) == ("jdoe", "jdoe@example.com", "Jane")


@pytest.mark.parametrize(
    "make",
    [
        lambda app: _token(app, secret="x" * 40),                  # wrong secret
        lambda app: _token(app, lifetime=-120),                     # expired
        lambda app: _token(app, lifetime=48 * 3600),                # valid too long
        lambda app: _token(app, aud=str(uuid.uuid4())),             # issued for another app
        lambda app: _token(app, email=None),                        # no email
        lambda app: _token(app, email="not-an-email"),
        lambda app: jwt.encode({"sub": "jdoe", "email": "j@example.com"}, SECRET, algorithm="HS256"),  # no exp/iat
        lambda app: _token(app).rsplit(".", 1)[0] + ".tampered",    # bad signature
    ],
)
def test_rejected_tokens(make):
    app = _app()
    with pytest.raises(HTTPException) as error:
        verify_identity(app, make(app))
    assert error.value.status_code == 401


def test_none_algorithm_is_rejected():
    app = _app()
    unsigned = jwt.encode({"sub": "jdoe", "email": "j@example.com", "iat": int(time.time()), "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(HTTPException):
        verify_identity(app, unsigned)


def test_frame_ancestors():
    assert frame_ancestors(_app()) == "frame-ancestors 'self' https://erp.example.com"
    assert frame_ancestors(_app(embed_enabled=False)) == "frame-ancestors 'self'"
    assert frame_ancestors(None) == "frame-ancestors 'self'"


def test_allowed_domain_validation():
    ok = EmbedSettings(enabled=True, auth="signed", allowed_domains=["https://ERP.example.com/", "https://*.example.com"])
    assert ok.allowed_domains == ["https://erp.example.com", "https://*.example.com"]
    for bad in ("erp.example.com", "javascript:alert(1)", "https://example.com/path", "https://a b.com", "*"):
        with pytest.raises(ValueError):
            EmbedSettings(enabled=True, auth="signed", allowed_domains=[bad])


def test_preview_identity_is_not_the_admin():
    """The preview must not claim the admin's email: that would bind their account to the preview's username."""
    admin = User(id=uuid.uuid4(), email="admin@example.com", name="Ada", username=None)
    username, email, name = preview_identity(admin)
    assert username != admin.email and email != admin.email
    assert username.startswith("preview:") and "Ada" in name
    IdentityClaims(sub=username, email=email, name=name)  # passes the same validation as a signed token
