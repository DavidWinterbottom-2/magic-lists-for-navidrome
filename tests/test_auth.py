"""Tests for the Microsoft Entra OIDC gate (backend/auth.py).

The gate protects every route so that, once Magic Lists is public, its
server-side Navidrome/AI credentials are never reachable anonymously. It is off
by default (AUTH_DISABLED unset) so a trusted LAN keeps working; these tests
drive both postures by reloading the module under different environments.

Run from the repo root:
    python -m unittest tests.test_auth
"""
import importlib
import os
import unittest
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import auth as auth_module

# Envelope of every var the module reads, so a reload starts from a clean slate.
_AUTH_ENV_KEYS = (
    "AUTH_DISABLED", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
    "ALLOWED_EMAILS", "SESSION_SECRET", "OIDC_REDIRECT_URI", "SESSION_HTTPS_ONLY",
)


@contextmanager
def _reloaded(**env):
    """Reload backend.auth with exactly the given env, then restore the ambient
    module afterwards so tests don't leak configuration into one another."""
    saved = {k: os.environ.get(k) for k in _AUTH_ENV_KEYS}
    for k in _AUTH_ENV_KEYS:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        importlib.reload(auth_module)
        yield auth_module
    finally:
        for k in _AUTH_ENV_KEYS:
            os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        importlib.reload(auth_module)


def _gated_app(mod):
    app = FastAPI()
    mod.install(app)

    @app.get("/")
    async def home():
        return {"page": "home"}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/playlists")
    async def api():
        return {"playlists": []}

    return app


class PublicPathTests(unittest.TestCase):
    def test_public_and_private_paths(self):
        with _reloaded() as mod:
            for path in ("/health", "/manifest.webmanifest", "/sw.js",
                         "/offline.html", "/static/app.js", "/auth/login"):
                self.assertTrue(mod._is_public(path), path)
            for path in ("/", "/api/playlists", "/manage"):
                self.assertFalse(mod._is_public(path), path)


class DisabledByDefaultTests(unittest.TestCase):
    def test_unset_leaves_the_app_open(self):
        # No AUTH_* set at all: the gate must be a no-op (LAN unchanged).
        with _reloaded() as mod:
            self.assertTrue(mod.AUTH_DISABLED)
            client = TestClient(_gated_app(mod))
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/playlists").status_code, 200)


class EnabledGateTests(unittest.TestCase):
    _CREDS = {
        "AUTH_DISABLED": "false",
        "AZURE_CLIENT_ID": "client-id",
        "AZURE_CLIENT_SECRET": "client-secret",
        "AZURE_TENANT_ID": "consumers",
        "SESSION_SECRET": "test-secret",
    }

    def test_unauthenticated_human_is_redirected_to_login(self):
        with _reloaded(**self._CREDS) as mod:
            client = TestClient(_gated_app(mod))
            resp = client.get("/", follow_redirects=False)
            self.assertIn(resp.status_code, (302, 307))
            self.assertEqual(resp.headers["location"], "/auth/login")

    def test_unauthenticated_api_call_gets_401(self):
        with _reloaded(**self._CREDS) as mod:
            client = TestClient(_gated_app(mod))
            resp = client.get("/api/playlists")
            self.assertEqual(resp.status_code, 401)

    def test_public_paths_stay_open_when_gated(self):
        with _reloaded(**self._CREDS) as mod:
            client = TestClient(_gated_app(mod))
            self.assertEqual(client.get("/health").status_code, 200)

    def test_login_route_exists_when_enabled(self):
        with _reloaded(**self._CREDS) as mod:
            client = TestClient(_gated_app(mod))
            # /auth/login is public and redirects out to Microsoft.
            resp = client.get("/auth/login", follow_redirects=False)
            self.assertIn(resp.status_code, (302, 307))
            self.assertIn("login.microsoftonline.com", resp.headers["location"])

    def test_missing_credentials_fail_closed(self):
        with _reloaded(AUTH_DISABLED="false") as mod:
            with self.assertRaises(RuntimeError):
                mod.install(FastAPI())


if __name__ == "__main__":
    unittest.main()
