"""Tests for where usage analytics is reported.

Upstream hardcoded a tag pointing at the original author's analytics instance,
so this fork reported every playlist build to a third party. The collector is
now configuration (a self-hosted Umami), and unset means nothing is collected.

Run from the repo root:
    python -m unittest tests.test_analytics_config
"""

import os
import unittest
from contextlib import contextmanager

from backend.main import analytics_config


@contextmanager
def _env(**values):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class AnalyticsConfigTests(unittest.TestCase):

    def test_unset_collects_nothing(self):
        with _env(ANALYTICS_SCRIPT_URL=None, ANALYTICS_WEBSITE_ID=None):
            self.assertEqual(
                analytics_config(),
                {"analytics_script_url": None, "analytics_website_id": None},
            )

    def test_both_set_is_reported(self):
        with _env(ANALYTICS_SCRIPT_URL="https://a.example.com/script.js",
                  ANALYTICS_WEBSITE_ID="abc-123"):
            config = analytics_config()
        self.assertEqual(config["analytics_script_url"], "https://a.example.com/script.js")
        self.assertEqual(config["analytics_website_id"], "abc-123")

    def test_half_configured_is_treated_as_off(self):
        # A script URL with no website id can't report anywhere useful
        with _env(ANALYTICS_SCRIPT_URL="https://a.example.com/script.js",
                  ANALYTICS_WEBSITE_ID=None):
            self.assertIsNone(analytics_config()["analytics_script_url"])
        with _env(ANALYTICS_SCRIPT_URL=None, ANALYTICS_WEBSITE_ID="abc-123"):
            self.assertIsNone(analytics_config()["analytics_website_id"])

    def test_whitespace_only_values_are_off(self):
        with _env(ANALYTICS_SCRIPT_URL="   ", ANALYTICS_WEBSITE_ID="  "):
            self.assertIsNone(analytics_config()["analytics_script_url"])

    def test_values_are_trimmed(self):
        with _env(ANALYTICS_SCRIPT_URL="  https://a.example.com/script.js  ",
                  ANALYTICS_WEBSITE_ID=" abc-123 "):
            config = analytics_config()
        self.assertEqual(config["analytics_script_url"], "https://a.example.com/script.js")
        self.assertEqual(config["analytics_website_id"], "abc-123")


if __name__ == "__main__":
    unittest.main()
