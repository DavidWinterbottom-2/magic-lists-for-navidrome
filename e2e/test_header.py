"""The header follows the shared winterbottom header rules.

One line at phone widths and every text size, no signed-in name, and the
account cluster ends with the shared Aa text-size menu then the theme toggle
(see LOOK-AND-FEEL.md "Header rules" in docker-infra/design-system).
"""

import pytest
from playwright.sync_api import expect

# The app bar; the mobile sidebar has its own <header>, which isn't the bar.
HEADER = "header:has(.mobile-toggle)"
CLUSTER = f"{HEADER} .mobile-toggle"


def set_text_size(page, size):
    page.locator(f"{CLUSTER} details.wb-textsize > summary").click()
    page.locator(f'[data-wb-text-size="{size}"]').click()


@pytest.mark.parametrize("width", [320, 375])
@pytest.mark.parametrize("size", ["normal", "large"])
def test_the_header_is_one_line(page, width, size):
    page.set_viewport_size({"width": width, "height": 720})
    page.goto("/")
    set_text_size(page, size)
    page.locator(f"{CLUSTER} details.wb-textsize").evaluate("d => d.open = false")

    header = page.locator(HEADER).bounding_box()
    assert header["height"] < 72, f"header is {header['height']}px tall"

    brand = page.locator(f"{HEADER} .ml-logo").bounding_box()
    controls = page.locator(f"{CLUSTER} > *")
    for i in range(controls.count()):
        box = controls.nth(i).bounding_box()
        brand_mid = brand["y"] + brand["height"] / 2
        mid = box["y"] + box["height"] / 2
        assert abs(mid - brand_mid) <= 8, f"control {i} is not on the brand's row"
        assert box["x"] + box["width"] <= width, f"control {i} overflows the viewport"

    # Nothing in the page scrolls sideways because of the bar.
    assert page.evaluate("document.documentElement.scrollWidth") <= width


def test_the_header_shows_no_signed_in_user(page):
    page.goto("/")
    expect(page.locator(".wb-who")).to_have_count(0)
    expect(page.locator(HEADER)).not_to_contain_text("signed in", ignore_case=True)


def test_controls_are_menu_then_text_size_then_theme_last(page):
    page.set_viewport_size({"width": 375, "height": 720})
    page.goto("/")
    controls = page.locator(f"{CLUSTER} > *")
    assert controls.count() == 3
    expect(controls.nth(0)).to_have_attribute("aria-label", "Toggle navigation")
    expect(controls.nth(1)).to_have_class("wb-textsize")
    expect(controls.nth(2)).to_have_attribute("data-wb-theme-toggle", "")


def test_the_aa_menu_does_not_show_the_track_list_disclosure_arrow(page):
    page.goto("/")
    summary = page.locator(f"{CLUSTER} details.wb-textsize > summary")
    content = summary.evaluate("s => getComputedStyle(s, '::before').content")
    assert content in ("none", "normal"), content
    box = summary.bounding_box()
    assert round(box["width"]) == 36 and round(box["height"]) == 36


def test_large_text_scales_the_root_and_survives_a_reload(page):
    page.goto("/")
    set_text_size(page, "large")
    expect(page.locator("html")).to_have_attribute("data-text-size", "large")
    root_px = page.evaluate("getComputedStyle(document.documentElement).fontSize")
    assert root_px == "18px"
    body_px = page.evaluate("getComputedStyle(document.body).fontSize")
    assert body_px == "18px", "body text is pinned in px, so it doesn't scale"

    page.reload()
    expect(page.locator("html")).to_have_attribute("data-text-size", "large")
    assert page.evaluate("getComputedStyle(document.documentElement).fontSize") == "18px"
