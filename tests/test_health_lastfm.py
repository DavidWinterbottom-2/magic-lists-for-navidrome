"""Tests for the optional Last.fm check on the system-check page.

Covers the offline, config-gating branches only (no key / key-without-username),
which return before any network call — so no live Last.fm API is required.

Run from the repo root:
    python -m unittest tests.test_health_lastfm
"""

import os
import unittest

from backend.services.health_check_service import HealthCheckService


class LastfmHealthCheckTests(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("LASTFM_API_KEY", "LASTFM_USERNAME")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    async def test_not_configured_is_info_and_never_an_error(self):
        result = await HealthCheckService()._check_lastfm()
        self.assertEqual(result["name"], "Last.fm Integration")
        self.assertEqual(result["status"], "info")  # optional → must not read as a failure
        self.assertIn("optional", result["message"].lower())

    async def test_key_without_username_is_a_warning(self):
        os.environ["LASTFM_API_KEY"] = "k"
        result = await HealthCheckService()._check_lastfm()
        self.assertEqual(result["status"], "warning")
        self.assertIn("LASTFM_USERNAME", result["suggestion"])

    async def test_optional_check_never_fails_the_overall_run(self):
        # A misconfigured (or unconfigured) optional integration must not be able
        # to set status "error", which is what run_checks fails all_passed on.
        os.environ.pop("LASTFM_API_KEY", None)
        no_key = await HealthCheckService()._check_lastfm()
        os.environ["LASTFM_API_KEY"] = "k"
        key_only = await HealthCheckService()._check_lastfm()
        self.assertNotEqual(no_key["status"], "error")
        self.assertNotEqual(key_only["status"], "error")


if __name__ == "__main__":
    unittest.main()
