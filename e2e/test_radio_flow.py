"""Building a Radio station through the UI.

Radio is the feature this fork exists for, and its rules are enforced after the
model returns rather than asked for in the prompt — so the useful end-to-end
assertion is not "a playlist appeared" but "the station obeys its guarantees".
Those are checked against what actually reached Navidrome.
"""

from fakes import fake_navidrome
from playwright.sync_api import expect


def open_radio(page):
    page.goto("/")
    page.locator('a[data-page="radio"]').first.click()
    expect(page.locator("#radio-content")).to_be_visible()
    # Wait for the page's fetches to settle before selecting. loadRadioArtists()
    # clears the current selection as it repopulates the list, so a selection
    # made while one is still in flight is silently undone and the form then
    # submits with no seed. Counting options isn't enough — the count is the
    # same before and after a repopulate.
    page.wait_for_load_state("networkidle")
    expect(page.locator("#radio-artist-select option")).to_have_count(4)


def test_the_seed_type_toggle_switches_between_artist_and_song(page):
    open_radio(page)
    expect(page.locator("#radio-artist-seed")).to_be_visible()
    # Asserting the closed state too: if the `.hidden` rule ever stops being
    # applied, every to_be_visible() below passes vacuously and this fails first.
    expect(page.locator("#radio-song-seed")).to_be_hidden()

    page.locator("#radio-seed-song").check()
    expect(page.locator("#radio-song-seed")).to_be_visible()

    expect(page.locator("#radio-artist-seed")).to_be_hidden()

    page.locator("#radio-seed-artist").check()
    expect(page.locator("#radio-artist-seed")).to_be_visible()
    expect(page.locator("#radio-song-seed")).to_be_hidden()


def test_a_station_opens_with_its_seed_and_caps_that_artist(page):
    before = set(fake_navidrome.STATE.playlists)

    open_radio(page)
    # Nothing to show before a station is built.
    expect(page.locator("#radio-results")).to_be_hidden()
    page.locator("#radio-artist-select").select_option(label="Alpha Waves")

    # Wait for the build to actually come back. The toast self-dismisses after
    # five seconds and the panel is rendered from the response, so anything
    # else here races the request.
    with page.expect_response(
        lambda r: "/api/create_radio_playlist" in r.url, timeout=60_000
    ) as response:
        page.locator("#create-radio-playlist-btn").click()
    assert response.value.status == 200, f"create returned {response.value.status}"

    expect(page.locator("#radio-results")).to_be_visible(timeout=30_000)

    created = set(fake_navidrome.STATE.playlists) - before
    assert len(created) == 1, f"expected exactly one new playlist, got {created}"
    station = fake_navidrome.STATE.playlists[created.pop()]
    entries = station["entry"]

    assert "Alpha Waves" in station["name"]
    assert entries, "station was created empty"

    # promote_seed_first: a station seeded from an artist opens with that artist.
    assert entries[0]["artistId"] == "ar-1", (
        f"station opened with {entries[0]['artist']!r}, not the seed"
    )

    # cap_seed_artist: the seed holds at most 20% of the station — it's
    # "artists like Alpha Waves", not a greatest-hits of Alpha Waves.
    seed_tracks = [e for e in entries if e["artistId"] == "ar-1"]
    assert len(seed_tracks) <= max(1, len(entries) // 5), (
        f"seed artist took {len(seed_tracks)} of {len(entries)} tracks"
    )

    # And the station is genuinely a mix, not one artist plus a token second.
    assert len({e["artistId"] for e in entries}) >= 2


def test_the_station_suggests_albums_the_library_does_not_have(page):
    open_radio(page)
    page.locator("#radio-artist-select").select_option(label="Gamma Ray Kids")

    with page.expect_response(
        lambda r: "/api/create_radio_playlist" in r.url, timeout=60_000
    ):
        page.locator("#create-radio-playlist-btn").click()

    results = page.locator("#radio-results")
    expect(results).to_be_visible(timeout=30_000)
    expect(page.locator("#radio-reasoning")).not_to_be_empty()

    # The suggestions are the point of the feature: what to buy so the next
    # station has more to work with.
    suggestions = page.locator("#radio-album-suggestions")
    expect(suggestions).to_contain_text("Delta Static")
    expect(suggestions).to_contain_text("Reverb Country")
