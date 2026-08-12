"""A fake Navidrome/Subsonic server for the end-to-end tests.

The e2e suite drives the real app in a real browser, which means the real
backend, which means something has to answer Navidrome's API. Pointing the tests
at a live server would make them depend on someone's music library — the results
would differ per machine and the suite would fail for reasons that aren't bugs.

So this serves a small, fixed library over the handful of Subsonic endpoints the
app actually calls. Playlists created through it are held in memory, so a test
can assert that creating a playlist really reached "Navidrome".

Deliberately stdlib-only (http.server + threading): the suite already needs
Playwright and a browser, and a fake this small doesn't justify a web framework.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# ── The library under test ───────────────────────────────────────────────────
# Three artists so a station has neighbours to draw on, and enough tracks each
# that a 25-track request is satisfiable without hitting the per-artist cap.

ARTISTS = [
    {"id": "ar-1", "name": "Alpha Waves", "albumCount": 2},
    {"id": "ar-2", "name": "Beta Signal", "albumCount": 2},
    {"id": "ar-3", "name": "Gamma Ray Kids", "albumCount": 2},
]

GENRES = ["Shoegaze", "Post-Rock"]

ALBUMS = {}
SONGS = {}


def _build_library():
    """Two albums of six tracks per artist, with varied plays and years."""
    song_n = 0
    for artist_index, artist in enumerate(ARTISTS):
        albums = []
        for album_index in range(2):
            album_id = f"al-{artist['id']}-{album_index}"
            year = 2008 + artist_index * 4 + album_index
            songs = []
            for track in range(6):
                song_n += 1
                song_id = f"so-{song_n}"
                song = {
                    "id": song_id,
                    "title": f"{artist['name']} Track {album_index * 6 + track + 1}",
                    "artist": artist["name"],
                    "artistId": artist["id"],
                    "album": f"{artist['name']} Album {album_index + 1}",
                    "albumId": album_id,
                    "year": year,
                    "genre": GENRES[artist_index % len(GENRES)],
                    # Descending plays so popularity ordering is deterministic.
                    "playCount": 100 - song_n,
                    "duration": 210,
                }
                songs.append(song)
                SONGS[song_id] = song
            ALBUMS[album_id] = {
                "id": album_id,
                "name": f"{artist['name']} Album {album_index + 1}",
                "artist": artist["name"],
                "artistId": artist["id"],
                "year": year,
                "song": songs,
            }
            albums.append({k: v for k, v in ALBUMS[album_id].items() if k != "song"})
        artist["_albums"] = albums


_build_library()

ALL_SONGS = list(SONGS.values())


def songs_for_artist(artist_id):
    return [s for s in ALL_SONGS if s["artistId"] == artist_id]


def ok(payload=None):
    body = {"status": "ok", "version": "1.16.1", "type": "navidrome"}
    body.update(payload or {})
    return {"subsonic-response": body}


class FakeNavidromeState:
    """Playlists created during a test run, so assertions can see them."""

    def __init__(self):
        self.playlists = {}
        self.counter = 0
        self.lock = threading.Lock()

    def create(self, name):
        with self.lock:
            self.counter += 1
            pid = f"pl-{self.counter}"
            self.playlists[pid] = {"id": pid, "name": name, "entry": []}
            return pid

    def add_tracks(self, pid, song_ids):
        with self.lock:
            playlist = self.playlists.setdefault(pid, {"id": pid, "name": "", "entry": []})
            playlist["entry"].extend(SONGS[s] for s in song_ids if s in SONGS)

    def remove_indexes(self, pid, indexes):
        with self.lock:
            playlist = self.playlists.get(pid)
            if playlist:
                keep = [e for i, e in enumerate(playlist["entry"]) if str(i) not in indexes]
                playlist["entry"] = keep


STATE = FakeNavidromeState()


def handle(path, query):
    """Answer one Subsonic call. Unknown endpoints get a bare ok."""
    first = lambda key, default=None: (query.get(key) or [default])[0]  # noqa: E731

    if path.endswith("/getArtists.view"):
        return ok({"artists": {"index": [
            {"name": "A", "artist": [{k: v for k, v in a.items() if k != "_albums"} for a in ARTISTS]}
        ]}})

    if path.endswith("/getArtist.view"):
        artist = next((a for a in ARTISTS if a["id"] == first("id")), None)
        if not artist:
            return {"subsonic-response": {"status": "failed",
                                          "error": {"code": 70, "message": "Artist not found"}}}
        return ok({"artist": {"id": artist["id"], "name": artist["name"],
                              "album": artist["_albums"]}})

    if path.endswith("/getAlbum.view"):
        album = ALBUMS.get(first("id"))
        return ok({"album": album}) if album else ok({"album": {}})

    if path.endswith("/getMusicFolders.view"):
        return ok({"musicFolders": {"musicFolder": [{"id": "1", "name": "Music"}]}})

    if path.endswith("/getGenres.view"):
        return ok({"genres": {"genre": [
            {"value": g, "songCount": len([s for s in ALL_SONGS if s["genre"] == g]), "albumCount": 2}
            for g in GENRES
        ]}})

    if path.endswith("/getSongsByGenre.view"):
        genre = first("genre", "")
        return ok({"songsByGenre": {"song": [s for s in ALL_SONGS if s["genre"] == genre]}})

    if path.endswith("/search3.view"):
        term = (first("query", "") or "").strip().lower()
        matches = [s for s in ALL_SONGS if term in s["title"].lower()] if term else ALL_SONGS
        return ok({"searchResult3": {
            "song": matches[: int(first("songCount", "20") or 20)],
            "artist": [{k: v for k, v in a.items() if k != "_albums"} for a in ARTISTS],
            "album": list(ALBUMS.values()),
        }})

    if path.endswith("/getSimilarSongs2.view"):
        # Everything except the seed artist's own tracks, which is what makes a
        # station look like "artists like X" rather than a greatest-hits of X.
        seed = first("id")
        pool = [s for s in ALL_SONGS if s["artistId"] != seed] + songs_for_artist(seed)
        return ok({"similarSongs2": {"song": pool[: int(first("count", "50") or 50)]}})

    if path.endswith("/getArtistInfo2.view"):
        seed = first("id")
        others = [a for a in ARTISTS if a["id"] != seed]
        return ok({"artistInfo2": {"similarArtist": [
            {"id": a["id"], "name": a["name"]} for a in others
        ]}})

    if path.endswith("/getSong.view"):
        song = SONGS.get(first("id"))
        return ok({"song": song}) if song else ok()

    if path.endswith("/getStarred.view"):
        return ok({"starred": {"song": ALL_SONGS[:3]}})

    if path.endswith("/createPlaylist.view"):
        pid = STATE.create(first("name", "Untitled"))
        return ok({"playlist": STATE.playlists[pid]})

    if path.endswith("/updatePlaylist.view"):
        pid = first("playlistId")
        to_add = query.get("songIdToAdd") or []
        if pid and to_add:
            STATE.add_tracks(pid, to_add)
        removals = query.get("songIndexToRemove") or []
        if pid and removals:
            STATE.remove_indexes(pid, removals)
        return ok()

    if path.endswith("/getPlaylist.view"):
        playlist = STATE.playlists.get(first("id"))
        return ok({"playlist": playlist or {"id": first("id"), "entry": []}})

    if path.endswith("/deletePlaylist.view"):
        STATE.playlists.pop(first("id"), None)
        return ok()

    return ok()


class Handler(BaseHTTPRequestHandler):
    def _respond(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        self._respond(handle(parsed.path, parse_qs(parsed.query)))

    def do_POST(self):
        if urlparse(self.path).path == "/auth/login":
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            # The app logs in for Subsonic credentials, then signs every later
            # call with them. The fake doesn't verify signatures — it is standing
            # in for Navidrome, not testing Navidrome's auth.
            self._respond({"token": "fake-jwt", "subsonicToken": "fake-token",
                           "subsonicSalt": "fake-salt", "username": "e2e"})
            return
        self._respond(ok())

    def log_message(self, *args):
        pass  # Keep pytest output readable.


def start(host="127.0.0.1", port=0):
    """Start the fake in a background thread. Returns (base_url, shutdown)."""
    # Threading rather than the single-threaded HTTPServer: the app issues
    # overlapping requests (an album lookup per album), and serialising them
    # would make the fake a bottleneck the real Navidrome isn't.
    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        # shutdown() only stops the serve loop; without server_close() the
        # listening socket stays open and every run leaks one per fake.
        server.shutdown()
        server.server_close()

    return f"http://{host}:{server.server_address[1]}", stop
