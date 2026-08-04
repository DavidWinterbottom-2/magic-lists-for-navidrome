"""Microsoft Entra ID (Azure AD) OIDC login.

Gates the whole app so that, once Magic Lists is exposed publicly, the
Navidrome credentials and AI key it holds server-side are never reachable
anonymously. Only identities whose email is on ``ALLOWED_EMAILS`` are let in.
Set ``AUTH_DISABLED=true`` to bypass entirely on a trusted LAN / Tailscale.

Uses the same environment-variable convention as the other winterbottom.xyz
apps (asset-summary, stories, nutrition), so one Azure app registration can be
reused — just add this service's redirect URI to it.

The gate defaults to **off** (``AUTH_DISABLED`` unset ⇒ disabled) so the existing
LAN deployment keeps working unchanged after an auto-update; set
``AUTH_DISABLED=false`` together with the Azure credentials before putting the
app on the public internet.
"""
from __future__ import annotations

import os
import secrets

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import FastAPI, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

# Fail OPEN by default: an unconfigured deployment stays LAN-only exactly as
# before rather than crash-looping. Public exposure is a deliberate opt-in
# (AUTH_DISABLED=false + Azure creds).
AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "true").lower() in ("1", "true", "yes")

# Personal Microsoft accounts (e.g. @hotmail.com) live in the fixed "consumers"
# tenant. Its discovery document returns a concrete issuer, whereas "common" /
# "organizations" return a templated "{tenantid}" issuer that authlib 1.3+
# (joserfc backend) cannot exact-match, raising InvalidClaimError on 'iss'.
# Matches the other winterbottom.xyz apps.
TENANT = os.environ.get("AZURE_TENANT_ID", "consumers")
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")  # optional explicit override
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "true").lower() in ("1", "true", "yes")

ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if e.strip()
}

# Paths that never require a login. The PWA install assets are public so that
# Chrome's WebAPK minter — which fetches the manifest, service worker and icons
# server-side, without the user's session cookie — can build the installed app.
# They are just the shell/branding, so exposing them anonymously leaks nothing;
# every route that returns library data lives under /api and stays gated.
_PUBLIC_PREFIXES = ("/auth/", "/static/")
_PUBLIC_EXACT = {
    "/health",
    "/manifest.webmanifest",
    "/sw.js",
    "/offline.html",
}


def _is_public(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES)


def _signed_out_html(ms_logout_url: str = "") -> str:
    """A small public confirmation page shown after sign-out.

    We deliberately do NOT redirect into a protected path here: bouncing
    through ``/auth/login`` while the Microsoft SSO session is still alive
    would silently re-authenticate the user, so "sign out" would look like a
    no-op. Landing on this public page keeps the user signed out until they
    explicitly choose to sign in again.
    """
    ms_link = (
        f'<p style="margin-top:1.1rem"><a href="{ms_logout_url}" '
        f'style="color:var(--ink-3);font-size:.85rem">'
        f"Also sign out of your Microsoft account</a></p>"
        if ms_logout_url else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="theme-color" content="#f3f5f8" media="(prefers-color-scheme: light)" />
  <meta name="theme-color" content="#0c0e12" media="(prefers-color-scheme: dark)" />
  <title>Signed out · Magic Lists</title>
  <script src="/static/winterbottom-theme.js"></script>
  <link rel="stylesheet" href="/static/winterbottom.css" />
  <link rel="stylesheet" href="/static/magiclists.css" />
</head>
<body>
  <div class="wb-login">
    <div class="wb-login-card">
      <span class="wb-brand__mark">~</span>
      <h1>Magic Lists</h1>
      <p>You're signed out — your session on this device has ended.</p>
      <a class="wb-btn wb-btn--primary" href="/auth/login" style="justify-content:center">Sign in again</a>
      {ms_link}
    </div>
  </div>
</body>
</html>"""


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if AUTH_DISABLED or _is_public(request.url.path):
            return await call_next(request)
        if request.session.get("user"):
            return await call_next(request)
        # Unauthenticated: API callers get a clean 401, humans get bounced to
        # the Microsoft login.
        if request.url.path.startswith("/api"):
            return JSONResponse({"error": "authentication required"}, status_code=401)
        return RedirectResponse("/auth/login")


def _redirect_uri(request: Request) -> str:
    if REDIRECT_URI:
        return REDIRECT_URI
    # Honour the reverse proxy's X-Forwarded-Proto (uvicorn is started with
    # --proxy-headers) so the callback URL is https in production.
    return str(request.url_for("auth_callback"))


def install(app: FastAPI) -> None:
    """Wire session + OIDC login into the app. Call right after creating it."""
    if AUTH_DISABLED:
        # No gate on a trusted LAN; the app behaves exactly as before.
        return

    missing = [n for n, v in (("AZURE_CLIENT_ID", CLIENT_ID),
                              ("AZURE_CLIENT_SECRET", CLIENT_SECRET),
                              ("AZURE_TENANT_ID", TENANT)) if not v]
    if missing:
        raise RuntimeError(
            "Auth is enabled but these env vars are missing: "
            + ", ".join(missing)
            + ". Set them, or set AUTH_DISABLED=true for a trusted LAN.")

    secret = SESSION_SECRET or secrets.token_urlsafe(32)
    if not SESSION_SECRET:
        print("WARNING: SESSION_SECRET not set; using a random per-process "
              "secret (logins will not survive a restart).")

    oauth = OAuth()
    oauth.register(
        name="microsoft",
        server_metadata_url=(
            f"https://login.microsoftonline.com/{TENANT}/v2.0/"
            f".well-known/openid-configuration"),
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        client_kwargs={"scope": "openid email profile"},
    )

    @app.get("/auth/login")
    async def auth_login(request: Request):
        return await oauth.microsoft.authorize_redirect(request, _redirect_uri(request))

    @app.get("/auth/callback", name="auth_callback")
    async def auth_callback(request: Request):
        try:
            token = await oauth.microsoft.authorize_access_token(request)
        except OAuthError as exc:
            return PlainTextResponse(f"Login failed: {exc.error}", status_code=401)
        info = token.get("userinfo") or {}
        email = (info.get("email") or info.get("preferred_username") or "").lower()
        if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
            return PlainTextResponse(
                f"Access denied for {email or 'this account'}.", status_code=403)
        request.session["user"] = email or "authenticated"
        return RedirectResponse("/")

    @app.get("/auth/logout")
    async def auth_logout(request: Request):
        request.session.pop("user", None)
        # Offer a best-effort federated logout link (ends the Microsoft SSO
        # session too), but never let a metadata hiccup break sign-out.
        ms_logout_url = ""
        try:
            meta = await oauth.microsoft.load_server_metadata()
            ms_logout_url = meta.get("end_session_endpoint") or ""
        except Exception:  # noqa: BLE001 - logout must always succeed
            ms_logout_url = ""
        return HTMLResponse(_signed_out_html(ms_logout_url))

    # Auth check runs inside the session context, so add it first (inner) and
    # SessionMiddleware second (outer) — the outermost middleware runs first.
    app.add_middleware(_AuthMiddleware)
    app.add_middleware(
        SessionMiddleware, secret_key=secret, same_site="lax",
        https_only=SESSION_HTTPS_ONLY)


def current_user(request: Request) -> str | None:
    if AUTH_DISABLED:
        return None
    return request.session.get("user")
