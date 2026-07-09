#!/usr/bin/env python3
"""
gen_placeholder_logos.py — deterministic placeholder brand logos for the clean-room projects (docs/26).

Emits a 512x512 PNG per project into assets/brand/. These are TEMPORARY placeholders so CurseForge
(mandatory avatar) and Modrinth (optional icon) can be created now; swap in real art later by dropping
a file with the same name and re-running reserve_slugs.py --icon.

Deterministic: same inputs -> same bytes (no randomness), so re-running is a no-op in git unless the
spec below changes.

Usage:  gen_placeholder_logos.py [--out <dir>]
"""

import argparse
import os

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_BOLD = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
SIZE = 512

# One entry per project: filename, monogram, label, background gradient (top->bottom), accent.
SPECS = {
    "gigafauna": {
        "monogram": "G", "label": "GIGAFAUNA",
        "bg_top": (26, 46, 30), "bg_bottom": (12, 20, 14), "accent": (63, 163, 77),
    },
    "matoulib": {
        "monogram": "m", "label": "MATOULIB",
        "bg_top": (24, 36, 56), "bg_bottom": (11, 17, 28), "accent": (58, 123, 213),
    },
    "gigafauna-pack": {
        "monogram": "GP", "label": "GIGAFAUNA PACK",
        "bg_top": (48, 38, 20), "bg_bottom": (24, 18, 9), "accent": (217, 154, 43),
    },
}


def vgradient(top, bottom):
    img = Image.new("RGB", (SIZE, SIZE), top)
    px = img.load()
    for y in range(SIZE):
        t = y / (SIZE - 1)
        row = tuple(round(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
        for x in range(SIZE):
            px[x, y] = row
    return img


def draw_logo(spec):
    img = vgradient(spec["bg_top"], spec["bg_bottom"])
    d = ImageDraw.Draw(img)
    accent = spec["accent"]

    # Accent ring framing the monogram.
    margin = 40
    d.ellipse([margin, margin, SIZE - margin, SIZE - margin - 70], outline=accent, width=10)

    # Monogram, centered in the ring.
    mono = spec["monogram"]
    fsize = 300 if len(mono) == 1 else 190
    font = ImageFont.truetype(FONT_BOLD, fsize)
    ring_cy = (margin + (SIZE - margin - 70)) / 2
    bbox = d.textbbox((0, 0), mono, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((SIZE / 2 - tw / 2 - bbox[0], ring_cy - th / 2 - bbox[1]), mono, font=font, fill=(240, 240, 240))

    # Label strip at the bottom.
    lfont = ImageFont.truetype(FONT_BOLD, 42)
    lbbox = d.textbbox((0, 0), spec["label"], font=lfont)
    lw = lbbox[2] - lbbox[0]
    d.text((SIZE / 2 - lw / 2 - lbbox[0], SIZE - 96), spec["label"], font=lfont, fill=accent)
    return img


def main():
    ap = argparse.ArgumentParser(description="Generate placeholder brand logos (docs/26).")
    ap.add_argument("--out", default=os.path.join(SCRIPT_DIR, "assets", "brand"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if not os.path.isfile(FONT_BOLD):
        raise SystemExit(f"font not found: {FONT_BOLD} (install ttf-dejavu)")
    for name, spec in SPECS.items():
        path = os.path.join(args.out, f"{name}.png")
        draw_logo(spec).save(path, "PNG", optimize=True)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
