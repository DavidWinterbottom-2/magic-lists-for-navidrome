# Browser end-to-end tests

REPO-STANDARDS §4 requires a web UI to have tests that drive the actual UI in a
browser. These do: they start the real app, open it in headless Chromium, and
exercise the flows a listener uses — create a "This Is" playlist, build a Radio
station, read the results.

```bash
pip install -r requirements-dev.txt
python -m pytest e2e/ --browser chromium          # headless
python -m pytest e2e/ --browser chromium --headed # watch it
python -m pytest e2e/ --browser chromium --slowmo 500 --headed
```

They live outside `tests/` on purpose: `tests/` is the fast unit suite that runs
on every push under both pytest and `unittest discover`, and it must not acquire
a browser dependency.

## Nothing reaches the network

Three things are faked, so a red suite means a broken app rather than a broken
dependency:

| Real thing | Stand-in | Why |
| --- | --- | --- |
| Navidrome | `fake_navidrome.py` | A fixed three-artist library, so results don't depend on whose music server is running. Created playlists are held in memory, so tests can assert that a playlist really reached it. |
| The AI provider | `fake_ai.py` | Wired in as `ollama` (the provider needing no key). The real curation pipeline runs — recipe, prompt, parsing, guarantees — and only the model's judgement is faked. |
| Tailwind / Preline / fonts CDNs | stubbed in `conftest.py` | The page loads them from public CDNs; tests shouldn't fail because a CDN is down. |

## Two things worth knowing before adding tests

**`.hidden` needs the stub.** The app shows and hides elements with
`classList.add/remove('hidden')`, and that class is Tailwind's. With Tailwind
stubbed there is no rule behind it, so a `to_be_visible()` assertion on such an
element passes instantly and tests *nothing*. `conftest.py` injects
`.hidden:not(select){display:none}` to make those assertions real. `select` is
excluded because those are the controls Preline would replace — the app hides
them by default, and keeping them visible leaves something to drive.

**Prefer durable signals to toasts.** The success toast dismisses itself after
five seconds, so asserting on it races the build. Wait for the results panel or
the playlists list instead.

## What's asserted

Beyond "the page rendered", the flow tests check the guarantees end to end —
that a station opens with its seed, that the seed artist stays under its share,
that a playlist reached Navidrome with the right tracks. Those are read from the
fake's in-memory state, which runs in the same process as the tests.
