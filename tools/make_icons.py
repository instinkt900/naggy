#!/usr/bin/env python3
"""Generate Naggy's PWA icons.

Naggy's mark is a white reminder **bell** on a warm amber rounded square — a
sibling to Sugar Daddy's blue-square/white-drop icon, but clearly its own thing
(different hue, different glyph). Everything is drawn from primitives at 4x
supersampling and downscaled with LANCZOS for clean anti-aliased edges, so the
icons are fully reproducible: re-run this script to regenerate them.

    python tools/make_icons.py

Writes into naggy/static/icons/: icon-192.png, icon-512.png,
icon-maskable-512.png (full-bleed, glyph in the safe zone), apple-touch-icon.png
(180px, opaque square).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "naggy" / "static" / "icons"
SS = 4  # supersample factor

AMBER_TOP = (255, 180, 84)    # #ffb454
AMBER_BOT = (240, 138, 28)    # #f08a1c
WHITE = (255, 255, 255, 255)

# Bell silhouette in a 0..100 unit box (drawn white, then mapped into a glyph box).
def _draw_bell(draw: ImageDraw.ImageDraw, box: tuple[float, float, float, float]) -> None:
    bx, by, g = box[0], box[1], box[2]

    def u(x: float) -> float:
        return bx + x / 100.0 * g

    def v(y: float) -> float:
        return by + y / 100.0 * g

    def circle(cx, cy, r, fill=WHITE):
        draw.ellipse([u(cx - r), v(cy - r), u(cx + r), v(cy + r)], fill=fill)

    # top knob + neck
    circle(50, 12, 5)
    draw.rectangle([u(47), v(12), u(53), v(26)], fill=WHITE)
    # rounded shoulders (top half-circle of the body)
    draw.pieslice([u(26), v(22), u(74), v(66)], start=180, end=360, fill=WHITE)
    # flaring skirt
    draw.polygon([(u(26), v(44)), (u(20), v(74)), (u(80), v(74)), (u(74), v(44))], fill=WHITE)
    # flared bottom rim (rounded)
    draw.rounded_rectangle([u(16), v(73), u(84), v(85)], radius=(6 / 100.0 * g), fill=WHITE)
    # clapper
    circle(50, 92, 5.5)


def _gradient_square(px: int) -> Image.Image:
    """Vertical amber gradient, fully opaque."""
    grad = Image.new("RGB", (1, px))
    for y in range(px):
        t = y / max(px - 1, 1)
        grad.putpixel((0, y), tuple(round(AMBER_TOP[i] + (AMBER_BOT[i] - AMBER_TOP[i]) * t) for i in range(3)))
    return grad.resize((px, px))


def make(size: int, *, rounded: bool, glyph_frac: float, opaque: bool) -> Image.Image:
    px = size * SS
    grad = _gradient_square(px).convert("RGBA")

    if rounded:
        mask = Image.new("L", (px, px), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1], radius=int(px * 0.22), fill=255)
        grad.putalpha(mask)

    base = grad if opaque or rounded else grad  # full-bleed square is just the gradient
    canvas = base.copy()

    # centre the glyph box
    g = px * glyph_frac
    box = ((px - g) / 2, (px - g) / 2, g)
    _draw_bell(ImageDraw.Draw(canvas), box)

    out = canvas.resize((size, size), Image.LANCZOS)
    if opaque:
        flat = Image.new("RGB", (size, size), AMBER_BOT)
        flat.paste(out, (0, 0), out)
        out = flat
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make(192, rounded=True, glyph_frac=0.66, opaque=False).save(OUT / "icon-192.png")
    make(512, rounded=True, glyph_frac=0.66, opaque=False).save(OUT / "icon-512.png")
    # maskable: full-bleed, glyph kept well inside the safe zone
    make(512, rounded=False, glyph_frac=0.56, opaque=True).save(OUT / "icon-maskable-512.png")
    # apple: opaque square (iOS applies its own mask)
    make(180, rounded=False, glyph_frac=0.64, opaque=True).save(OUT / "apple-touch-icon.png")
    print(f"wrote 4 icons to {OUT}")


if __name__ == "__main__":
    main()
