"""The app loads, navigates, and reports its own health.

These are the checks that fail first when something fundamental breaks — a
template that won't render, a script that throws on load, a backend that can't
reach Navidrome.
"""

from playwright.sync_api import expect


def test_the_app_loads(page):
    page.goto("/")
    expect(page).to_have_title("Magic Lists")
    expect(page.locator("#welcome-content")).to_be_visible()


def test_the_service_worker_and_manifest_are_served(page):
    # The PWA shell is public even behind the auth gate, so a 404 here is a
    # broken install prompt rather than a broken page.
    for path in ("/manifest.webmanifest", "/sw.js"):
        response = page.request.get(path)
        assert response.status == 200, f"{path} returned {response.status}"


def test_navigating_to_each_playlist_type_shows_its_form(page):
    page.goto("/")
    for nav_target, section in (
        ("this-is-artist", "#this-is-content"),
        ("radio", "#radio-content"),
        ("genre-mix", "#genre-mix-content"),
        ("re-discover", "#rediscover-content"),
        ("playlists", "#manage-playlists-content"),
    ):
        page.locator(f'a[data-page="{nav_target}"]').first.click()
        expect(page.locator(section)).to_be_visible()


def test_the_system_check_page_reports_a_reachable_library(page):
    page.goto("/system-check")
    body = page.locator("body")
    # The fake Navidrome answers auth and getArtists, so the page should report
    # a working connection rather than the failure state.
    expect(body).to_contain_text("Navidrome", timeout=30_000)
    expect(body).not_to_contain_text("Invalid username or password")
