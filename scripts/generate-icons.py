#!/usr/bin/env python3
"""Generate the Magic Lists app icons from the winterbottom brand mark.

The design system draws a brand mark as a rounded square filled with the amber
accent and a bold monospace monogram in `--accent-ink` (see `.wb-brand__mark` in
winterbottom.css). The installable-app icon is that same mark at icon sizes, so
the PWA on a home screen matches the header inside the app.

Amber is taken from the dark-theme accent rather than the light one: an app icon
sits on whatever wallpaper the user has, and the brighter tone stays legible on
both. Regenerate with:  python scripts/generate-icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ACCENT = (240, 178, 74)      # --accent (dark theme)
ACCENT_INK = (36, 23, 3)     # --accent-ink (dark theme)
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "static" / "assets"
SUPERSAMPLE = 4              # draw large, downscale — cheap anti-aliasing


def _draw_mark(size: int, radius_ratio: float, text_ratio: float) -> Image.Image:
    """Render the mark at `size` px: rounded amber tile + centred "ML"."""
    scale = size * SUPERSAMPLE
    img = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle(
        [(0, 0), (scale - 1, scale - 1)],
        radius=int(scale * radius_ratio),
        fill=ACCENT,
    )

    font = ImageFont.truetype(MONO_BOLD, int(scale * text_ratio))
    left, top, right, bottom = draw.textbbox((0, 0), "ML", font=font)
    draw.text(
        ((scale - (right - left)) / 2 - left, (scale - (bottom - top)) / 2 - top),
        "ML",
        font=font,
        fill=ACCENT_INK,
    )

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Standard icons: rounded tile, generous monogram.
    for size in (192, 512):
        _draw_mark(size, radius_ratio=0.22, text_ratio=0.42).save(
            OUT_DIR / f"icon-{size}.png"
        )

    # Apple touch icon: iOS applies its own mask, so ship square corners.
    _draw_mark(180, radius_ratio=0.0, text_ratio=0.42).save(
        OUT_DIR / "apple-touch-icon.png"
    )

    # Maskable: full bleed, monogram inside the 80% safe circle Android may crop to.
    _draw_mark(512, radius_ratio=0.0, text_ratio=0.30).save(
        OUT_DIR / "icon-maskable-512.png"
    )

    # Favicon: multi-resolution .ico so browser tabs and bookmarks stay crisp.
    _draw_mark(64, radius_ratio=0.22, text_ratio=0.44).save(
        OUT_DIR / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)]
    )

    for path in sorted(OUT_DIR.glob("icon-*.png")):
        print(f"  {path.name}  {path.stat().st_size:,} bytes")
    print(f"  apple-touch-icon.png  {(OUT_DIR / 'apple-touch-icon.png').stat().st_size:,} bytes")
    print(f"  favicon.ico  {(OUT_DIR / 'favicon.ico').stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
