"""The embeddable chat widget.

A site adds one <script> tag (widget.js). It shows a chat button and opens the
chat in an iframe served by Chat Builder, so the site's styles can't affect it
and the site can't read the conversation. Browsers only allow the iframe on the
app's allowed domains (Content-Security-Policy frame-ancestors).

Sign-in modes, per app:
- login:  people sign in with their Chat Builder account inside the widget
- signed: the embedding site's server signs a short-lived token (JWT, HS256)
          with the app's secret, naming the person (username and email); they
          are created or updated here and given access automatically
- public: anonymous visitors

Widget sessions are bearer tokens of kind "embed": they work for chatting with
that one app and never for the dashboard.
"""

import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from html import escape

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select

from app.crypto import decrypt, encrypt
from app.deps import AdminUser, DbSession, current_user
from app.models import AppAccess, ChatApp, User
from app.routers.apps import owned_app
from app.routers.users import public_base
from app.security import authenticate, create_session

router = APIRouter(tags=["embed"])

EMBED_SESSION = timedelta(hours=12)
# Longest validity accepted for a signed identity token (exp - iat).
MAX_TOKEN_LIFETIME = timedelta(hours=24)
AUTH_MODES = ("login", "signed", "public")
ORIGIN = re.compile(r"^https?://(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:\d{1,5})?$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def secret_context(app_id: uuid.UUID) -> str:
    return f"embed_secret:{app_id}"


def embeddable_app(db: DbSession, app_id: uuid.UUID) -> ChatApp:
    app = db.get(ChatApp, app_id)
    if app is None or not app.embed_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat app not found")
    return app


def frame_ancestors(app: ChatApp | None) -> str:
    """CSP for the widget iframe: only the app's allowed domains (and Chat Builder itself) may embed it."""
    sources = ["'self'"]
    if app is not None and app.embed_enabled:
        sources += [d for d in app.allowed_domains if ORIGIN.match(d)]
    return "frame-ancestors " + " ".join(sources)


# ------------------------------------------------------------- admin: deploy


class EmbedSettings(BaseModel):
    enabled: bool
    auth: str
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)
    color: str = "#0f172a"

    @field_validator("auth")
    @classmethod
    def _auth(cls, value: str) -> str:
        if value not in AUTH_MODES:
            raise ValueError("Choose login, signed or public")
        return value

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, value: list[str]) -> list[str]:
        cleaned = []
        for entry in value:
            origin = entry.strip().lower().rstrip("/")
            if not origin:
                continue
            if not ORIGIN.match(origin):
                raise ValueError(f'"{entry}" isn\'t a site address like https://erp.example.com')
            cleaned.append(origin)
        return list(dict.fromkeys(cleaned))

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if not COLOR.match(value):
            raise ValueError("Use a color like #0f172a")
        return value


class EmbedOut(EmbedSettings):
    app_id: uuid.UUID
    has_secret: bool
    script_url: str
    snippet: str
    public_url_configured: bool


def _embed_out(app: ChatApp, request: Request) -> EmbedOut:
    from app.config import get_settings

    base = public_base(request)
    script_url = f"{base}/embed/widget.js?app={app.id}"
    token_attr = ' data-user-token="SIGNED_TOKEN"' if app.embed_auth == "signed" else ""
    return EmbedOut(
        enabled=app.embed_enabled,
        auth=app.embed_auth,
        allowed_domains=app.allowed_domains,
        color=app.widget_color,
        app_id=app.id,
        has_secret=app.encrypted_embed_secret is not None,
        script_url=script_url,
        snippet=f'<script src="{script_url}"{token_attr} async></script>',
        public_url_configured=bool(get_settings().public_url),
    )


@router.get("/api/apps/{app_id}/embed")
def get_embed(app_id: uuid.UUID, request: Request, db: DbSession, user: AdminUser) -> EmbedOut:
    return _embed_out(owned_app(db, user, app_id), request)


@router.put("/api/apps/{app_id}/embed")
def update_embed(app_id: uuid.UUID, body: EmbedSettings, request: Request, db: DbSession, user: AdminUser) -> EmbedOut:
    app = owned_app(db, user, app_id)
    app.embed_enabled = body.enabled
    app.embed_auth = body.auth
    app.allowed_domains = body.allowed_domains
    app.widget_color = body.color
    if body.auth == "signed" and app.encrypted_embed_secret is None:
        app.encrypted_embed_secret = encrypt(secrets.token_urlsafe(32), secret_context(app.id))
    db.commit()
    return _embed_out(app, request)


@router.get("/api/apps/{app_id}/embed/secret")
def reveal_secret(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> dict:
    app = owned_app(db, user, app_id)
    if app.encrypted_embed_secret is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No secret yet")
    return {"secret": decrypt(app.encrypted_embed_secret, secret_context(app.id))}


@router.post("/api/apps/{app_id}/embed/secret")
def rotate_secret(app_id: uuid.UUID, db: DbSession, user: AdminUser) -> dict:
    """A new secret. Tokens signed with the old one stop working immediately."""
    app = owned_app(db, user, app_id)
    secret = secrets.token_urlsafe(32)
    app.encrypted_embed_secret = encrypt(secret, secret_context(app.id))
    db.commit()
    return {"secret": secret}


def sign_identity(app: ChatApp, username: str, email: str, name: str = "") -> str:
    """What an embedding site's server does; used for the preview page and tests."""
    now = datetime.now(UTC)
    claims = {"sub": username, "email": email, "name": name, "aud": str(app.id), "iat": now, "exp": now + timedelta(hours=1)}
    return jwt.encode(claims, decrypt(app.encrypted_embed_secret, secret_context(app.id)), algorithm="HS256")


@router.get("/embed/preview/{app_id}", include_in_schema=False)
def preview(app_id: uuid.UUID, request: Request, db: DbSession) -> HTMLResponse:
    """A test page that embeds the widget, signed in as the admin viewing it."""
    user = current_user(request, db)
    app = owned_app(db, user, app_id)
    if not app.embed_enabled:
        return HTMLResponse("<p>Turn on embedding for this app first.</p>", status_code=400)
    token = ""
    if app.embed_auth == "signed":
        token = sign_identity(app, username=user.username or user.email, email=user.email, name=user.name)
    script = f"{public_base(request)}/embed/widget.js?app={app.id}"
    token_attr = f' data-user-token="{escape(token)}"' if token else ""
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><title>Widget preview: {escape(app.name)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; background: #f1f5f9; margin: 0; padding: 40px; color: #334155">
<h1 style="margin-top:0">Widget preview</h1>
<p>This page stands in for your site. Click the chat button in the bottom right.</p>
<p style="color:#64748b">Sign-in mode: <strong>{escape(app.embed_auth)}</strong>{" (signed in as " + escape(user.email) + ")" if token else ""}</p>
<script src="{escape(script)}"{token_attr} async></script>
</body></html>"""
    )


# --------------------------------------------------------- the widget itself


class WidgetConfig(BaseModel):
    name: str
    description: str
    welcome_message: str
    color: str
    auth: str
    allowed_origins: list[str]


class WidgetSession(BaseModel):
    token: str
    name: str
    email: str


class EmbedLogin(BaseModel):
    email: str
    password: str


class Identify(BaseModel):
    token: str = Field(min_length=10, max_length=8000)


class IdentityClaims(BaseModel):
    sub: str = Field(min_length=1, max_length=200)
    email: EmailStr
    name: str = Field(default="", max_length=200)


@router.get("/api/embed/{app_id}/config")
def widget_config(app_id: uuid.UUID, db: DbSession) -> WidgetConfig:
    app = embeddable_app(db, app_id)
    return WidgetConfig(
        name=app.name,
        description=app.description,
        welcome_message=app.welcome_message,
        color=app.widget_color,
        auth=app.embed_auth,
        allowed_origins=app.allowed_domains,
    )


def _grant(db: DbSession, user: User, app: ChatApp) -> None:
    if not user.is_admin and db.get(AppAccess, (app.id, user.id)) is None:
        db.add(AppAccess(app_id=app.id, user_id=user.id))


@router.post("/api/embed/{app_id}/login")
def widget_login(app_id: uuid.UUID, body: EmbedLogin, db: DbSession) -> WidgetSession:
    app = embeddable_app(db, app_id)
    if app.embed_auth != "login":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This chat doesn't use Chat Builder sign-in.")
    user = authenticate(db, body.email, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if not user.is_admin and db.get(AppAccess, (app.id, user.id)) is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have access to this chat. Ask an admin.")
    token = create_session(db, user, kind="embed", app_id=app.id, lifetime=EMBED_SESSION)
    return WidgetSession(token=token, name=user.name, email=user.email)


def verify_identity(app: ChatApp, token: str) -> IdentityClaims:
    if app.encrypted_embed_secret is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This chat has no signing secret yet.")
    secret = decrypt(app.encrypted_embed_secret, secret_context(app.id))
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            # The audience, if the site includes one, must be this app.
            audience=str(app.id) if "aud" in unverified else None,
            options={"require": ["exp", "iat", "sub", "email"]},
            leeway=60,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in token has expired. Reload the page.") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in token isn't valid.") from None
    if claims["exp"] - claims["iat"] > MAX_TOKEN_LIFETIME.total_seconds():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in tokens may be valid for at most 24 hours.")
    try:
        return IdentityClaims(sub=str(claims["sub"]), email=claims["email"], name=str(claims.get("name") or ""))
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "The sign-in token needs a username (sub) and a valid email.") from None


def provision(db: DbSession, claims: IdentityClaims) -> User:
    """Finds or creates the person a signed token names. The username is the stable identity."""
    username = claims.sub.strip()
    email = claims.email.lower()
    user = db.scalar(select(User).where(User.username == username))
    email_owner = db.scalar(select(User).where(User.email == email))
    if user is None:
        if email_owner is not None and email_owner.username not in (None, username):
            raise HTTPException(status.HTTP_409_CONFLICT, "That email belongs to someone with a different username.")
        if email_owner is not None:
            # Links an existing Chat Builder account to this username.
            user = email_owner
            user.username = username
        else:
            user = User(email=email, username=username, name=claims.name, source="embed")
            db.add(user)
            db.flush()
    elif user.email != email:
        if email_owner is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "That email belongs to someone else.")
        user.email = email
    if claims.name and claims.name != user.name:
        user.name = claims.name
    return user


@router.post("/api/embed/{app_id}/identify")
def widget_identify(app_id: uuid.UUID, body: Identify, db: DbSession) -> WidgetSession:
    app = embeddable_app(db, app_id)
    if app.embed_auth != "signed":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This chat doesn't use signed sign-in.")
    user = provision(db, verify_identity(app, body.token))
    if user.blocked:
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your access to this chat has been turned off.")
    _grant(db, user, app)
    db.commit()
    token = create_session(db, user, kind="embed", app_id=app.id, lifetime=EMBED_SESSION)
    return WidgetSession(token=token, name=user.name, email=user.email)


@router.post("/api/embed/{app_id}/visitor")
def widget_visitor(app_id: uuid.UUID, db: DbSession) -> dict:
    app = embeddable_app(db, app_id)
    if app.embed_auth != "public":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This chat requires signing in.")
    return {"visitor_id": secrets.token_urlsafe(32)}


WIDGET_JS = r"""(function () {
  "use strict";
  var config = __CONFIG__;
  var script = document.currentScript;
  if (!config) { console.warn("Chat Builder: this chat app isn't available for embedding."); return; }
  if (window.ChatBuilder && window.ChatBuilder.__loaded) return;

  var token = script && script.getAttribute("data-user-token");
  var frame, panel, button, ready = false, open = false;

  function css(el, styles) { for (var k in styles) el.style[k] = styles[k]; }
  var mobile = window.matchMedia("(max-width: 640px)").matches;

  button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", "Open chat: " + config.name);
  button.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  css(button, { position: "fixed", right: "20px", bottom: "20px", width: "56px", height: "56px", borderRadius: "50%",
    border: "none", background: config.color, cursor: "pointer", boxShadow: "0 4px 14px rgba(0,0,0,.25)", zIndex: 2147483000,
    display: "flex", alignItems: "center", justifyContent: "center" });

  panel = document.createElement("div");
  css(panel, { position: "fixed", zIndex: 2147483000, display: "none", background: "white", overflow: "hidden",
    boxShadow: "0 12px 40px rgba(0,0,0,.25)" });
  if (mobile) css(panel, { inset: "0" });
  else css(panel, { right: "20px", bottom: "88px", width: "390px", height: "min(620px, calc(100vh - 110px))", borderRadius: "14px" });

  function send(message) { if (frame && ready) frame.contentWindow.postMessage(message, config.origin); }

  function ensureFrame() {
    if (frame) return;
    frame = document.createElement("iframe");
    frame.src = config.origin + "/embed/app/" + config.appId;
    frame.title = config.name;
    frame.allow = "clipboard-write";
    css(frame, { width: "100%", height: "100%", border: "0" });
    panel.appendChild(frame);
  }

  function setOpen(value) {
    open = value;
    if (open) ensureFrame();
    panel.style.display = open ? "block" : "none";
    button.style.display = open && mobile ? "none" : "flex";
    button.setAttribute("aria-label", (open ? "Close chat: " : "Open chat: ") + config.name);
  }

  window.addEventListener("message", function (event) {
    if (!frame || event.source !== frame.contentWindow || event.origin !== config.origin) return;
    var data = event.data || {};
    if (data.type === "cb:ready") {
      ready = true;
      send({ type: "cb:identity", token: token || null });
    } else if (data.type === "cb:close") {
      setOpen(false);
    }
  });

  button.addEventListener("click", function () { setOpen(!open); });
  function mount() { document.body.appendChild(panel); document.body.appendChild(button); }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  window.ChatBuilder = {
    __loaded: true,
    open: function () { setOpen(true); },
    close: function () { setOpen(false); },
    toggle: function () { setOpen(!open); },
    // For sites that sign tokens later (or refresh them): ChatBuilder.identify(token)
    identify: function (newToken) { token = newToken; send({ type: "cb:identity", token: token }); }
  };
})();
"""


@router.get("/embed/widget.js", include_in_schema=False)
def widget_js(request: Request, db: DbSession, app: uuid.UUID | None = None) -> Response:
    chat_app = db.get(ChatApp, app) if app else None
    config = None
    if chat_app is not None and chat_app.embed_enabled:
        config = {
            "appId": str(chat_app.id),
            "name": chat_app.name,
            "color": chat_app.widget_color,
            "origin": public_base(request),
        }
    body = WIDGET_JS.replace("__CONFIG__", json.dumps(config))
    return Response(body, media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})
