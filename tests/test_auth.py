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
        "ALLOWED_EMAILS": "david@example.com",
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

    def test_empty_allow_list_refuses_to_start(self):
        # An empty ALLOWED_EMAILS used to admit any account the tenant could
        # sign in; with "consumers" that is any Microsoft account in the world.
        creds = {k: v for k, v in self._CREDS.items() if k != "ALLOWED_EMAILS"}
        with _reloaded(**creds) as mod:
            with self.assertRaisesRegex(RuntimeError, "ALLOWED_EMAILS is empty"):
                mod.install(FastAPI())

    def test_multi_tenant_alias_refuses_to_start(self):
        for tenant in ("common", "Organizations"):
            with _reloaded(**{**self._CREDS, "AZURE_TENANT_ID": tenant}) as mod:
                with self.assertRaisesRegex(RuntimeError, "admits any Entra tenant"):
                    mod.install(FastAPI())


class AllowListTests(unittest.TestCase):
    def test_matches_case_insensitively(self):
        self.assertTrue(auth_module.is_allowed("David@Example.com", {"david@example.com"}))
        self.assertFalse(auth_module.is_allowed("intruder@example.com", {"david@example.com"}))

    def test_empty_list_or_email_admits_no_one(self):
        self.assertFalse(auth_module.is_allowed("anyone@example.com", set()))
        self.assertFalse(auth_module.is_allowed("", {"david@example.com"}))

    def test_single_tenant_with_a_list_is_fine(self):
        for tenant in ("consumers", "8cc87aa5-d9f6-43e0-aa12-00133c5a98d3"):
            self.assertEqual(auth_module.config_errors(tenant, {"d@x.com"}), [])


if __name__ == "__main__":
    unittest.main()
