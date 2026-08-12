"""Integration tests: the real API routes, end to end, in one process.

The unit suite covers the pure logic and the browser suite covers the UI. This
covers the seam between them — routes, the Navidrome client, the database and
the curation pipeline wired together — which is where a change to one layer
breaks another without either layer's own tests noticing.

Nothing is mocked at the Python level: the app talks HTTP to the same fake
Navidrome and fake AI provider the browser tests use, so the client code under
test is the code that ships.

Run from the repo root:
    python -m unittest tests.test_api_integration
    python -m pytest tests/test_api_integration.py
"""

import os
import tempfile
import unittest

from fakes import fake_ai, fake_navidrome


def _configure_environment():
    """Point the app at the fakes before anything constructs a client."""
    navidrome_url, stop_navidrome = fake_navidrome.start()
    ai_url, stop_ai = fake_ai.start()
    tmp = tempfile.TemporaryDirectory()

    os.environ.update({
        "NAVIDROME_URL": navidrome_url,
        "NAVIDROME_USERNAME": "integration",
        "NAVIDROME_PASSWORD": "integration-password",
        "DATABASE_PATH": os.path.join(tmp.name, "integration.db"),
        "LOG_LEVEL": "ERROR",
        # `ollama` is the provider that needs no API key; the fake answers at
        # OLLAMA_BASE_URL, so the real curation pipeline runs offline.
        "AI_PROVIDER": "ollama",
        "AI_MODEL": "fake-model",
        "OLLAMA_BASE_URL": f"{ai_url}/v1/chat/completions",
        "OLLAMA_TIMEOUT": "30",
        "AUTH_DISABLED": "true",
        "ANALYTICS_SCRIPT_URL": "",
        "ANALYTICS_WEBSITE_ID": "",
        "LASTFM_API_KEY": "",
        "LASTFM_USERNAME": "",
    })
    return tmp, stop_navidrome, stop_ai


class ApiTestCase(unittest.TestCase):
    """Base case: one app, one database, shared across a module's tests."""

    @classmethod
    def setUpClass(cls):
        cls._tmp, cls._stop_navidrome, cls._stop_ai = _configure_environment()

        # Imported after the environment is set, and its lazily-built clients
        # reset, so they pick up the fakes rather than anything a previous test
        # module left behind.
        from fastapi.testclient import TestClient

        from backend import main

        main.navidrome_client = None
        main.ai_client = None
        main.lastfm_client = None
        main.db_manager = None

        cls.main = main
        cls.client_context = TestClient(main.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls._stop_navidrome()
        cls._stop_ai()
        cls._tmp.cleanup()

    def create_this_is(self, artist_id="ar-1", length=25):
        return self.client.post("/api/create_playlist", json={
            "artist_ids": [artist_id], "playlist_length": length,
        })

    def create_radio(self, seed_id="ar-1", seed_type="artist", length=25):
        return self.client.post("/api/create_radio_playlist", json={
            "seed_type": seed_type, "seed_id": seed_id, "playlist_length": length,
        })


class LibraryReadTests(ApiTestCase):
    """The routes that just read Navidrome — the whole client path is exercised."""

    def test_health_is_dependency_free(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_artists_come_back_from_the_library(self):
        response = self.client.get("/api/artists")
        self.assertEqual(response.status_code, 200)
        names = {a["name"] for a in response.json()}
        self.assertEqual(names, {"Alpha Waves", "Beta Signal", "Gamma Ray Kids"})

    def test_genres_come_back_from_the_library(self):
        response = self.client.get("/api/genres")
        self.assertEqual(response.status_code, 200)
        # The route normalises Subsonic's {value, songCount} to {name, songCount}.
        self.assertTrue(any(g.get("name") == "Shoegaze" for g in response.json()))

    def test_song_search_finds_a_seed(self):
        response = self.client.get("/api/songs", params={"q": "Alpha Waves Track 1"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json())

    def test_music_folders_are_listed(self):
        response = self.client.get("/api/music-folders")
        self.assertEqual(response.status_code, 200)

    def test_the_health_check_reports_a_working_connection(self):
        response = self.client.get("/api/health-check")
        self.assertEqual(response.status_code, 200)
        checks = response.json().get("checks", [])
        self.assertTrue(checks, "health check returned no checks")
        by_name = {c["name"]: c for c in checks}
        self.assertIn("Navidrome Authentication", by_name)
        self.assertEqual(by_name["Navidrome Authentication"]["status"], "success")


class ThisIsCreationTests(ApiTestCase):
    def test_a_playlist_is_created_in_navidrome_and_recorded_locally(self):
        before = set(fake_navidrome.STATE.playlists)

        response = self.create_this_is("ar-1")

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["songs"])

        created = set(fake_navidrome.STATE.playlists) - before
        self.assertEqual(len(created), 1)
        playlist = fake_navidrome.STATE.playlists[created.pop()]
        self.assertEqual({e["artistId"] for e in playlist["entry"]}, {"ar-1"})

    def test_the_curators_reasoning_is_stored_not_the_fallback_message(self):
        body = self.create_this_is("ar-2").json()
        self.assertIn("reasoning", body)
        self.assertNotIn("AI service was unavailable", body.get("reasoning") or "")

    def test_an_unknown_artist_is_reported_rather_than_silently_empty(self):
        response = self.create_this_is("does-not-exist")
        self.assertGreaterEqual(response.status_code, 400)


class RadioCreationTests(ApiTestCase):
    def test_a_station_opens_with_its_seed_and_caps_that_artist(self):
        response = self.create_radio("ar-1", length=25)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        songs = body["songs"]
        self.assertTrue(songs)
        # The guarantees radio.py enforces after curation, checked through the
        # route rather than by calling the functions directly.
        self.assertEqual(songs[0]["artist"], "Alpha Waves")
        seed_tracks = [s for s in songs if s["artist"] == "Alpha Waves"]
        self.assertLessEqual(len(seed_tracks), max(1, len(songs) // 5))
        self.assertGreaterEqual(len({s["artist"] for s in songs}), 2)

    def test_a_station_reports_how_it_was_built(self):
        body = self.create_radio("ar-2").json()
        shortfall = body.get("shortfall")
        self.assertIsNotNone(shortfall)
        self.assertIn("requested", shortfall)
        self.assertIn("delivered", shortfall)

    def test_album_suggestions_come_back_with_the_station(self):
        body = self.create_radio("ar-3").json()
        self.assertTrue(body.get("album_suggestions"))

    def test_a_song_seed_is_accepted(self):
        response = self.create_radio("so-1", seed_type="song")
        self.assertEqual(response.status_code, 200, response.text)

    def test_an_invalid_seed_type_is_rejected(self):
        response = self.create_radio("ar-1", seed_type="album")
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_seed_is_a_404(self):
        response = self.create_radio("nope")
        self.assertEqual(response.status_code, 404)


class ReDiscoverTests(ApiTestCase):
    """Re-Discover is the least-covered path and the one with two generations
    of endpoint still live, so both are exercised here."""

    def test_recommendations_are_generated(self):
        # Regression: this returned 500 because days_since_last_play is
        # computed as an int while the response schema declared it a string,
        # so any never-played track failed validation.
        response = self.client.get("/api/rediscover-weekly")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("tracks", response.json())

    def test_v2_says_so_plainly_when_there_is_too_little_history(self):
        # The fake library has no listening history, which is the case v2 is
        # designed to refuse. It should say why rather than return an empty
        # playlist or fall over.
        response = self.client.get("/api/rediscover-weekly-v2")
        self.assertEqual(response.status_code, 404)
        self.assertIn("listening history", response.json()["detail"])

    def test_a_rediscover_playlist_can_be_created(self):
        response = self.client.post("/api/create-rediscover-playlist",
                                    json={"playlist_length": 10, "refresh_frequency": "weekly"})
        self.assertEqual(response.status_code, 200, response.text)


class PlaylistManagementTests(ApiTestCase):
    def test_created_playlists_are_listed_rebuilt_and_deleted(self):
        created = self.create_this_is("ar-3").json()
        navidrome_id = created["navidrome_playlist_id"]

        listed = self.client.get("/api/playlists")
        self.assertEqual(listed.status_code, 200)
        row = next(p for p in listed.json() if p["navidrome_playlist_id"] == navidrome_id)
        self.assertTrue(row["songs"])

        # Rebuild through the same path the scheduler uses.
        rebuilt = self.client.post(f"/api/playlists/{row['id']}/recreate", json={})
        self.assertEqual(rebuilt.status_code, 200, rebuilt.text)

        deleted = self.client.delete(f"/api/playlists/{row['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertNotIn(navidrome_id, fake_navidrome.STATE.playlists)

    def test_deleting_something_that_is_not_there_is_reported(self):
        self.assertGreaterEqual(self.client.delete("/api/playlists/999999").status_code, 400)


class RecipeAndSchedulerTests(ApiTestCase):
    def test_every_registered_recipe_validates(self):
        response = self.client.get("/api/recipes/validate")
        self.assertEqual(response.status_code, 200)
        invalid = {name: r["errors"] for name, r in response.json().items() if not r["valid"]}
        self.assertEqual(invalid, {}, f"recipes failing validation: {invalid}")

    def test_recipes_report_real_metadata(self):
        response = self.client.get("/api/recipes")
        self.assertEqual(response.status_code, 200)
        radio = response.json()["radio"]
        self.assertTrue(radio["uses_llm"])
        self.assertTrue(radio["recipe_id"])

    def test_the_scheduler_reports_its_state(self):
        response = self.client.get("/api/scheduler/status")
        self.assertEqual(response.status_code, 200)
        self.assertIn("scheduler_running", response.json())

    def test_the_ai_provider_in_use_is_reported(self):
        response = self.client.get("/api/ai-model-info")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
