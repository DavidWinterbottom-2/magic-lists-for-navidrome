"""A fake OpenAI-compatible AI provider for the end-to-end tests.

Without this the tests would have to run with no AI configured — but that path
raises for "This Is" and Genre Mix (the provider rejects a missing key before
any curation happens), so the flows a listener actually uses would go untested.

Standing in for the model instead means the real curation pipeline runs: the
recipe is applied, the prompt is built, the response is parsed, and the
guarantees are enforced over the result. Only the model's judgement is faked.

It is wired in as the `ollama` provider, which is the one that needs no API key
and takes its URL from OLLAMA_BASE_URL.

The reply is derived from the request: the prompt carries the candidate pool as
indexed JSON, so the fake selects real indices out of it rather than guessing.
A fixed list would break the moment a recipe changed how many candidates it
sends.
"""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Enough to fill the default 25-track request when the pool allows it.
MAX_SELECTED = 25

INDEX = re.compile(r'"index":\s*(\d+)')

ALBUM_SUGGESTIONS = [
    {"artist": "Delta Static", "album": "Reverb Country", "year": 2011,
     "reason": "Shares the seed's guitar haze."},
    {"artist": "Epsilon Drift", "album": "Long Way Down", "year": 2016,
     "reason": "Same slow-build arrangements."},
]


def choose(payload):
    """Pick indices out of whatever candidate pool the prompt carried."""
    prompt = " ".join(
        m.get("content", "") for m in payload.get("messages", []) if isinstance(m, dict)
    )
    indices = [int(i) for i in INDEX.findall(prompt)]
    return sorted(set(indices))[:MAX_SELECTED]


def reply_for(payload):
    """A curation reply, always including nested objects.

    Every reply carries `album_suggestions` even though only Radio asks for them,
    and that is deliberate: models routinely return more than the prompt
    requested, and the nested objects are what broke the "This Is" parser — it
    extracted JSON with a non-greedy regex that truncated at the first inner
    brace, failed to parse, and fell back to play-count ordering while reporting
    "AI service was unavailable".

    Sending nesting on every request means the e2e suite exercises that path on
    every flow. If the extractor regresses, these tests fail rather than quietly
    passing on fallback output.
    """
    return json.dumps({
        "track_ids": choose(payload),
        "reasoning": "A steady run through the library's quieter corners, "
                     "opening with the familiar and drifting outward.",
        "album_suggestions": ALBUM_SUGGESTIONS,
    })


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}

        body = json.dumps({
            "id": "fake-completion",
            "object": "chat.completion",
            "model": payload.get("model", "fake-model"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": reply_for(payload)},
                "finish_reason": "stop",
            }],
        }).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Keep pytest output readable.


def start(host="127.0.0.1", port=0):
    """Start the fake in a background thread. Returns (base_url, shutdown)."""
    # Threading rather than the single-threaded HTTPServer: the app issues
    # overlapping requests (an album lookup per album), and serialising them
    # would make the fake a bottleneck the real Navidrome isn't.
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://{host}:{server.server_address[1]}", server.shutdown
