"""Render the landing page's social card into web/og.png.

The card is drawn rather than screenshotted, for the same reason the CLI banner
is drawn: a screenshot of a terminal is illegible in a 400px timeline preview,
while the mark, the claim and the command survive being shrunk.

Fonts come from the OS, so this is a build step run by hand on a machine that
has them, not part of CI:

    python scripts/build_og.py

Pillow is already a dependency of Migratify itself, so there is nothing extra
to install.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 630
PAD = 78

BG = (8, 9, 12)
INK = (233, 236, 242)
DIM = (152, 162, 177)
FAINT = (105, 114, 127)
CYAN = (95, 227, 212)
GREEN = (88, 212, 124)
AMBER = (240, 192, 90)
RED = (240, 101, 95)

OUT = Path(__file__).resolve().parents[1] / "web" / "og.png"

# Windows first, then macOS, then a common Linux pair. The wordmark wants a
# high-contrast serif to echo Instrument Serif on the page; everything else is
# monospaced, like the tool.
SERIF = ["C:/Windows/Fonts/georgia.ttf", "/System/Library/Fonts/Supplemental/Georgia.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"]
SERIF_I = ["C:/Windows/Fonts/georgiai.ttf", "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf"]
MONO = ["C:/Windows/Fonts/consola.ttf", "/System/Library/Fonts/Menlo.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
MONO_B = ["C:/Windows/Fonts/consolab.ttf", "/System/Library/Fonts/Menlo.ttc",
          "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"]


def font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise SystemExit(
        "No usable font found. Install one of:\n  " + "\n  ".join(candidates)
    )


def glow(img: Image.Image, cx: int, cy: int, radius: int, colour: tuple[int, int, int],
         strength: float) -> Image.Image:
    """Additive radial glow, so the background is a surface and not a slab.

    The mask is a blurred ellipse drawn at canvas size rather than a pasted
    gradient tile: a tile leaves a visible square seam where its corners stop
    being transparent, which is exactly the kind of edge nobody can unsee.
    """
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse(
        [cx - radius // 2, cy - radius // 2, cx + radius // 2, cy + radius // 2],
        fill=int(255 * strength),
    )
    mask = mask.filter(ImageFilter.GaussianBlur(radius // 2))
    layer = Image.new("RGB", img.size, (0, 0, 0))
    layer.paste(Image.new("RGB", img.size, colour), (0, 0), mask)
    return ImageChops.add(img, layer)


def mark(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, fill=CYAN,
         hole=BG) -> None:
    """The block-art octagon from the CLI banner, with its white centre."""
    s = size
    c = s * 0.32  # corner cut
    draw.polygon(
        [
            (x + c, y), (x + s - c, y),
            (x + s, y + c), (x + s, y + s - c),
            (x + s - c, y + s), (x + c, y + s),
            (x, y + s - c), (x, y + c),
        ],
        fill=fill,
    )
    if hole is None:
        return
    r = s * 0.19
    mid = (x + s / 2, y + s / 2)
    draw.ellipse([mid[0] - r, mid[1] - r, mid[0] + r, mid[1] + r], fill=hole)
    r2 = s * 0.075
    draw.ellipse([mid[0] - r2, mid[1] - r2, mid[0] + r2, mid[1] + r2], fill=INK)


def main() -> int:
    img = Image.new("RGB", (W, H), BG)

    # grid, then two glows over it
    grid = ImageDraw.Draw(img)
    for x in range(0, W, 68):
        grid.line([(x, 0), (x, H)], fill=(18, 21, 26))
    for y in range(0, H, 68):
        grid.line([(0, y), (W, y)], fill=(18, 21, 26))

    img = glow(img, 60, 20, 940, (24, 96, 90), 0.52)
    img = glow(img, 1180, 660, 720, (92, 72, 24), 0.26)

    # the mark again, oversized and half off-canvas: a graphic anchor for the
    # empty right side that still reads at thumbnail size
    stamp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    mark(ImageDraw.Draw(stamp), 905, 96, 470, fill=(95, 227, 212, 26), hole=None)
    img = Image.alpha_composite(img.convert("RGBA"), stamp).convert("RGB")

    d = ImageDraw.Draw(img)

    f_word = font(SERIF, 92)
    f_claim = font(SERIF_I, 60)
    f_lede = font(MONO, 26)
    f_small = font(MONO, 21)
    f_cmd = font(MONO_B, 26)
    f_foot = font(MONO, 20)

    # --- wordmark ---------------------------------------------------------
    mark(d, PAD, PAD + 4, 74)
    d.text((PAD + 74 + 26, PAD - 12), "Migratify", font=f_word, fill=INK)

    # --- the claim --------------------------------------------------------
    d.text((PAD, 212), "Without the wrong songs.", font=f_claim, fill=CYAN)

    d.text(
        (PAD, 306),
        "Migrate playlists both ways between",
        font=f_lede,
        fill=DIM,
    )
    d.text((PAD, 342), "Spotify and YouTube Music.", font=f_lede, fill=DIM)

    # --- the three outcomes ----------------------------------------------
    y = 412
    x = PAD
    for colour, text in (
        (GREEN, "auto-accepted"),
        (AMBER, "asks you"),
        (RED, "never guessed"),
    ):
        d.ellipse([x, y + 7, x + 9, y + 16], fill=colour)
        d.text((x + 20, y), text, font=f_small, fill=colour)
        x += int(d.textlength(text, font=f_small)) + 62

    # --- the command ------------------------------------------------------
    box = [PAD, 476, W - PAD, 546]
    d.rounded_rectangle(box, radius=10, fill=(15, 18, 24), outline=(40, 46, 56))
    d.text((PAD + 22, 496), "$", font=f_cmd, fill=(29, 126, 119))
    d.text(
        (PAD + 46, 496),
        "migratify migrate https://open.spotify.com/playlist/...",
        font=f_cmd,
        fill=(207, 233, 229),
    )

    # --- footer -----------------------------------------------------------
    foot = "MIT licensed  ·  v0.3.1  ·  github.com/DecoudJuan/Migratify"
    d.text((PAD, 578), foot, font=f_foot, fill=FAINT)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
