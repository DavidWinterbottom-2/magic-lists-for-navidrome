"""Creating a "This Is" playlist, start to finish, through the UI.

The assertions deliberately reach past the browser at the end: a playlist that
renders in the app but never reached Navidrome isn't a playlist. The fake
Navidrome runs in this process, so its state is readable directly.
"""

import fake_navidrome
from playwright.sync_api import expect


def open_this_is(page):
    page.goto("/")
    page.locator('a[data-page="this-is-artist"]').first.click()
    expect(page.locator("#this-is-content")).to_be_visible()
    return page.locator("#artist-search-select")


def test_the_artist_list_comes_from_the_library(page):
    select = open_this_is(page)
    # Three artists from the fake library, plus the placeholder option.
    expect(select.locator("option")).to_have_count(4)
    expect(select).to_contain_text("Alpha Waves")
    expect(select).to_contain_text("Gamma Ray Kids")


def test_creating_a_playlist_reaches_navidrome_and_the_ui(page):
    before = set(fake_navidrome.STATE.playlists)

    select = open_this_is(page)
    select.select_option(label="Beta Signal")

    # Wait for the request itself to come back before doing anything else.
    # Clicking create and then navigating straight to the playlists tab races
    # the in-flight build, which fails intermittently and looks like a bug in
    # the app rather than in the test.
    with page.expect_response(
        lambda r: "/api/create_playlist" in r.url, timeout=60_000
    ) as response:
        page.locator("#create-artist-playlist-btn").click()
    assert response.value.status == 200, f"create returned {response.value.status}"

    page.locator('a[data-page="playlists"]').first.click()
    manage = page.locator("#manage-playlists-content")
    expect(manage).to_contain_text("This Is: Beta Signal", timeout=30_000)

    # It exists in "Navidrome", named for the artist, holding that artist's tracks.
    created = set(fake_navidrome.STATE.playlists) - before
    assert len(created) == 1, f"expected exactly one new playlist, got {created}"
    playlist = fake_navidrome.STATE.playlists[created.pop()]
    assert "Beta Signal" in playlist["name"]
    assert playlist["entry"], "playlist was created empty"
    assert {e["artistId"] for e in playlist["entry"]} == {"ar-2"}

    # And the app shows it, with the curator's write-up rather than a fallback.
    expect(manage).to_contain_text("12 tracks")
    expect(manage).not_to_contain_text("AI service was unavailable")


def test_submitting_without_an_artist_does_not_create_anything(page):
    before = set(fake_navidrome.STATE.playlists)

    open_this_is(page)
    page.locator("#create-artist-playlist-btn").click()
    page.wait_for_timeout(1500)

    expect(page.locator("#toast-container")).not_to_contain_text("Playlist created")
    assert set(fake_navidrome.STATE.playlists) == before
