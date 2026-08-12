"""Fixtures for the browser end-to-end suite.

Each run starts a fake Navidrome (see fake_navidrome.py) and a real instance of
the app pointed at it, then drives that app in a headless browser. Nothing here
reaches the network: no live Navidrome, no AI provider, no CDN.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fakes import fake_ai, fake_navidrome  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# The app's markup pulls Tailwind, Preline and web fonts from public CDNs. Tests
# must not depend on those being up — a red suite should mean a broken app, not a
# broken CDN — so they're served locally instead.
#
# Stubbed rather than aborted: the page has an inline `tailwind.config = {...}`
# that throws ReferenceError if the CDN script simply fails, which would be a
# page error the suite reports on every single test. A stub that defines the
# object keeps the page clean while still not fetching anything.
#
# Preline is left undefined on purpose. Every call to it in app.js is guarded by
# `if (window.HSSelect)`, so the native <select> elements it would decorate stay
# plain — and without Tailwind's stylesheet the `.hidden` class doesn't hide
# them, which is what lets these tests drive the real form controls.
EXTERNAL_HOSTS = ("cdn.tailwindcss.com", "cdn.jsdelivr.net",
                  "fonts.googleapis.com", "fonts.gstatic.com")

# The stub also supplies `.hidden` for the specific panels the tests assert on.
# The app toggles those with `classList.add/remove('hidden')`, and that class is
# Tailwind's — with Tailwind stubbed there's no rule behind it, so
# `to_be_visible()` passes instantly and the assertion tests nothing.
#
# Scoped by id rather than as a blanket `.hidden` rule, because Tailwind's
# responsive variants share the class: the desktop nav is `hidden md:block`, and
# a blanket rule hides it — no `md:block` exists here to override — which makes
# every navigation in the suite time out. Add an id here when a new test needs
# to assert on a class-toggled panel.
HIDDEN_PANELS = ("#radio-results", "#radio-artist-seed", "#radio-song-seed",
                 "#radio-shortfall", "#radio-song-selected")

TAILWIND_STUB = """
window.tailwind = { config: {} };
(function () {
  var style = document.createElement('style');
  style.textContent = '%s { display: none !important; }';
  document.head.appendChild(style);
})();
""" % ", ".join(f"{sel}.hidden" for sel in HIDDEN_PANELS)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(url, timeout=60):
    """Poll a URL until it answers, or fail with the last error seen."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception as e:  # connection refused while it boots
            last = e
        time.sleep(0.25)
    raise RuntimeError(f"{url} never became ready ({last})")


@pytest.fixture(scope="session")
def navidrome_url():
    base_url, shutdown = fake_navidrome.start()
    yield base_url
    shutdown()


@pytest.fixture(scope="session")
def ai_url():
    base_url, shutdown = fake_ai.start()
    yield base_url
    shutdown()


@pytest.fixture(scope="session")
def app_server(navidrome_url, ai_url):
    """The real app, on a throwaway database, talking to the fake Navidrome."""
    port = free_port()
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "NAVIDROME_URL": navidrome_url,
            "NAVIDROME_USERNAME": "e2e",
            "NAVIDROME_PASSWORD": "e2e-password",
            "DATABASE_PATH": str(Path(tmp) / "e2e.db"),
            "LOG_LEVEL": "ERROR",
            "PYTHONPATH": str(REPO_ROOT),
            # Curation runs against a fake model (see fake_ai.py) rather than
            # being switched off: with no provider configured, "This Is" and
            # Genre Mix raise before curation starts, so the flows a listener
            # actually uses would never be exercised. `ollama` is the provider
            # that needs no key and takes its URL from the environment.
            "AI_PROVIDER": "ollama",
            "AI_API_KEY": "",
            "AI_MODEL": "fake-model",
            "OLLAMA_BASE_URL": f"{ai_url}/v1/chat/completions",
            "OLLAMA_TIMEOUT": "30",
            # Auth off, as on a trusted LAN. The login gate has its own tests.
            "AUTH_DISABLED": "true",
            # Analytics need both vars set; leaving them unset keeps the page
            # free of a script tag pointing at a host that isn't there.
            "ANALYTICS_SCRIPT_URL": "",
            "ANALYTICS_WEBSITE_ID": "",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
            cwd=REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            wait_for(f"{base_url}/health")
        except Exception:
            proc.terminate()
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"app failed to start:\n{output}")
        yield base_url
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="session")
def base_url(app_server):
    """Consumed by pytest-playwright, so tests can use relative paths."""
    return app_server


@pytest.fixture(autouse=True)
def stub_external_requests(page):
    """Serve every off-origin request locally, so no test touches the network."""
    def route(handler_route):
        url = handler_route.request.url
        if not any(host in url for host in EXTERNAL_HOSTS):
            handler_route.continue_()
            return
        if "tailwindcss" in url:
            handler_route.fulfill(status=200, content_type="application/javascript",
                                  body=TAILWIND_STUB)
        elif url.endswith(".js") or "jsdelivr" in url:
            handler_route.fulfill(status=200, content_type="application/javascript", body="")
        else:
            handler_route.fulfill(status=200, content_type="text/css", body="")

    page.route("**/*", route)
    yield


@pytest.fixture(autouse=True)
def fail_on_page_errors(page):
    """Surface uncaught JS exceptions as test failures.

    A flow can appear to pass while the console fills with errors; this makes a
    broken script fail the test that provoked it rather than the next one.
    """
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    yield
    assert not errors, "uncaught JavaScript errors:\n" + "\n".join(errors)
