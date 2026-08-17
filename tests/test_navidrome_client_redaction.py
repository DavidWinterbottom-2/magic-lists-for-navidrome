"""Subsonic credentials must be stripped before logging (backend/navidrome_client.py).

The Subsonic token `t` and salt `s` authenticate every request; a log line that
prints raw request params leaks them to stdout/logs. `_redact_auth` is the single
choke point every params-logging site goes through, so this guards against the
leak reappearing.

Run from the repo root:
    python -m unittest tests.test_navidrome_client_redaction
"""
import unittest

from backend.navidrome_client import NavidromeClient


class RedactAuthTest(unittest.TestCase):
    def test_strips_token_and_salt(self):
        params = {"u": "me", "t": "secret-token", "s": "salt-value", "v": "1.16.1", "id": "42"}
        redacted = NavidromeClient._redact_auth(params)
        self.assertNotIn("t", redacted)
        self.assertNotIn("s", redacted)
        # Non-credential params are preserved so the debug log stays useful.
        self.assertEqual(redacted["u"], "me")
        self.assertEqual(redacted["v"], "1.16.1")
        self.assertEqual(redacted["id"], "42")

    def test_does_not_mutate_input(self):
        params = {"u": "me", "t": "secret", "s": "salt"}
        NavidromeClient._redact_auth(params)
        self.assertEqual(params["t"], "secret")  # original dict left intact


if __name__ == "__main__":
    unittest.main()
