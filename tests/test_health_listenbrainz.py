"""Tests for the optional ListenBrainz check on the system-check page.

All four states (unconfigured / user not found / reachable / unreachable) with a
stubbed httpx client. None may report "error", which is what fails startup.

    python -m pytest tests/test_health_listenbrainz.py
"""

import os
import unittest
from unittest import mock

import httpx

from backend.services import health_check_service as hcs
from backend.services.health_check_service import HealthCheckService


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._payload


def fake_client(resp=None, raises=None):
    class C:
        calls = []

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **k):
            C.calls.append(url)
            if raises:
                raise raises
            return resp
    return C


class ListenBrainzHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("LISTENBRAINZ_USERNAME", "LISTENBRAINZ_URL")}
        for k in self._saved:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    async def _check(self, client_cls=None):
        with mock.patch.object(hcs.httpx, "AsyncClient", client_cls or fake_client(FakeResp())):
            return await HealthCheckService()._check_listenbrainz()

    async def test_unconfigured_is_info(self):
        r = await self._check()
        self.assertEqual(r["name"], "ListenBrainz Integration")
        self.assertEqual(r["status"], "info")
        self.assertIn("optional", r["message"].lower())

    async def test_user_not_found_is_warning(self):
        os.environ["LISTENBRAINZ_USERNAME"] = "nobody"
        r = await self._check(fake_client(FakeResp(404)))
        self.assertEqual(r["status"], "warning")
        self.assertIn("nobody", r["message"])

    async def test_reachable_is_success_with_count(self):
        os.environ["LISTENBRAINZ_USERNAME"] = "me"
        cls = fake_client(FakeResp(200, {"payload": {"count": 2}}))
        r = await self._check(cls)
        self.assertEqual(r["status"], "success")
        self.assertIn("2 listens", r["message"])
        self.assertTrue(cls.calls[0].endswith("/1/user/me/listen-count"))

    async def test_zero_listens_suggests_scrobbling(self):
        os.environ["LISTENBRAINZ_USERNAME"] = "me"
        r = await self._check(fake_client(FakeResp(200, {"payload": {"count": 0}})))
        self.assertEqual(r["status"], "success")
        self.assertIn("scrobbling", r["suggestion"])

    async def test_unreachable_is_warning_not_error(self):
        os.environ["LISTENBRAINZ_USERNAME"] = "me"
        r = await self._check(fake_client(raises=httpx.ConnectError("down")))
        self.assertEqual(r["status"], "warning")

    async def test_never_error_in_any_state(self):
        for user, cls in (("", None), ("me", fake_client(FakeResp(500))),
                          ("me", fake_client(FakeResp(200, {"x": 1})))):
            os.environ["LISTENBRAINZ_USERNAME"] = user
            self.assertNotEqual((await self._check(cls))["status"], "error")


if __name__ == "__main__":
    unittest.main()
