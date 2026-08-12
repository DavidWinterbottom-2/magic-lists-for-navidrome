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
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    """The JSON body this particular recipe asked for.

    Only Radio asks for album suggestions, and only Radio's parser tolerates the
    nested objects they need — "This Is" extracts its JSON with a non-greedy
    `\\{.*?"track_ids".*?\\}`, which truncates at the first inner brace and fails
    to parse. Answering in the shape the prompt requested is what a real model
    does, and it keeps the fake from exercising a path no recipe produces.
    """
    prompt = " ".join(
        m.get("content", "") for m in payload.get("messages", []) if isinstance(m, dict)
    )
    reply = {
        "track_ids": choose(payload),
        "reasoning": "A steady run through the library's quieter corners, "
                     "opening with the familiar and drifting outward.",
    }
    if "album_suggestions" in prompt:
        reply["album_suggestions"] = ALBUM_SUGGESTIONS
    return json.dumps(reply)


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
    server = HTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://{host}:{server.server_address[1]}", server.shutdown
