#!/usr/bin/env python3
"""uv_template.py — emit a labeled UV-unwrap template PNG from bedrock .geo.json models.

Answers "which face of which bone lives WHERE in the texture PNG" — the box-UV layout is
otherwise invisible when repainting a texture outside Blockbench.

Usage:
  python3 uv_template.py <model.geo.json | dir> [more paths...] [options]

Options:
  --out DIR      output directory (default: alongside each input, <name>.uvtemplate.png)
  --scale N      pixels per texel (default 8)
  --under PNG    draw the template OVER an existing texture (repaint aid; single input only)
  --no-grid      skip the per-texel grid
  --alpha N      face fill alpha 0-255 (default 70; 0 = outlines only)

Face letters: U=up D=down N=north(front) S=south(back) E=east W=west.
Box-UV layout per cube (origin u,v ; w,h,d = size x,y,z in texels):
        [ .d. ][ .w. ][ .w. ]
  v     [     ][  U  ][  D  ]   height d
  v+d   [  E  ][  N  ][  W  ][  S  ]   height h  (widths d,w,d,w)
Down face is sampled V-flipped by the engine; the template marks its rect, not the flip.

No packenv dependency: all paths come from CLI args. Only needs Pillow.
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Distinct, colorblind-friendly-ish cycle (bone -> color)
PALETTE = [
    (230, 25, 75), (60, 180, 75), (0, 130, 200), (245, 130, 48),
    (145, 30, 180), (70, 240, 240), (240, 50, 230), (210, 245, 60),
    (0, 128, 128), (220, 190, 255), (170, 110, 40), (128, 0, 0),
    (128, 128, 0), (0, 0, 128), (250, 190, 212), (255, 215, 180),
]

FACE_ORDER = ["up", "down", "east", "north", "west", "south"]
FACE_LETTER = {"up": "U", "down": "D", "north": "N", "south": "S", "east": "E", "west": "W"}


def load_geometry(path: Path):
    """Return (identifier, tex_w, tex_h, bones[]) from modern or legacy bedrock geo json."""
    data = json.loads(path.read_text())
    if "minecraft:geometry" in data:
        geo = data["minecraft:geometry"][0]
        desc = geo.get("description", {})
        ident = desc.get("identifier", path.stem)
        tw = int(desc.get("texture_width", 64))
        th = int(desc.get("texture_height", 64))
        bones = geo.get("bones", [])
    else:  # legacy: {"geometry.<name>": {"texturewidth":..,"bones":[..]}}
        key = next((k for k in data if k.startswith("geometry.")), None)
        if key is None:
            raise ValueError(f"{path}: no minecraft:geometry nor geometry.<name> root")
        geo = data[key]
        ident = key
        tw = int(geo.get("texturewidth", 64))
        th = int(geo.get("textureheight", 64))
        bones = geo.get("bones", [])
    return ident, tw, th, bones


def box_uv_rects(u, v, w, h, d):
    """Standard bedrock/MC box unwrap. Returns {face: (x0,y0,x1,y1)} in texels."""
    return {
        "up":    (u + d,         v,     u + d + w,         v + d),
        "down":  (u + d + w,     v,     u + d + 2 * w,     v + d),
        "east":  (u,             v + d, u + d,             v + d + h),
        "north": (u + d,         v + d, u + d + w,         v + d + h),
        "west":  (u + d + w,     v + d, u + d + w + d,     v + d + h),
        "south": (u + d + w + d, v + d, u + d + 2 * w + d, v + d + h),
    }


def cube_faces(cube):
    """Yield (face, rect_texels, flip_note) for one cube (box-UV or per-face)."""
    sx, sy, sz = cube["size"]
    uv = cube.get("uv", [0, 0])
    if isinstance(uv, list):
        for face, rect in box_uv_rects(uv[0], uv[1], sx, sy, sz).items():
            if rect[0] == rect[2] or rect[1] == rect[3]:
                continue  # zero-area face (flat cube side)
            yield face, rect, ""
    else:  # per-face object: {"north": {"uv":[x,y], "uv_size":[w,h]}, ...}
        for face in FACE_ORDER:
            spec = uv.get(face)
            if not spec:
                continue
            x, y = spec["uv"]
            fw, fh = spec.get("uv_size", [sx, sy])  # engine default: face-sized
            note = "flip" if fw < 0 or fh < 0 else ""
            x0, x1 = sorted((x, x + fw))
            y0, y1 = sorted((y, y + fh))
            if x0 == x1 or y0 == y1:
                continue
            yield face, (x0, y0, x1, y1), note


def render(path: Path, out_dir: Path | None, scale: int, under: Path | None,
           grid: bool, alpha: int) -> Path:
    ident, tw, th, bones = load_geometry(path)
    W, H = tw * scale, th * scale

    if under:
        base = Image.open(under).convert("RGBA").resize((W, H), Image.NEAREST)
    else:
        base = Image.new("RGBA", (W, H), (24, 24, 24, 255))
        # checkerboard so unused texture space is obvious
        px = base.load()
        for ty in range(th):
            for tx in range(tw):
                if (tx + ty) % 2:
                    for dy in range(scale):
                        for dx in range(scale):
                            px[tx * scale + dx, ty * scale + dy] = (34, 34, 34, 255)

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.load_default(size=max(8, scale))
        small = ImageFont.load_default(size=max(7, scale * 3 // 4))
    except TypeError:  # older Pillow: no size kwarg
        font = small = ImageFont.load_default()

    warnings = []
    for bi, bone in enumerate(bones):
        color = PALETTE[bi % len(PALETTE)]
        bname = bone.get("name", f"bone{bi}")
        for ci, cube in enumerate(bone.get("cubes", []) or []):
            mirror = " (mirror)" if cube.get("mirror") else ""
            for face, (x0, y0, x1, y1), note in cube_faces(cube):
                r = [x0 * scale, y0 * scale, x1 * scale - 1, y1 * scale - 1]
                if x0 < 0 or y0 < 0 or x1 > tw or y1 > th:
                    warnings.append(f"{bname}.{face} out of texture bounds: {(x0, y0, x1, y1)}")
                draw.rectangle(r, fill=color + (alpha,), outline=color + (255,))
                letter = FACE_LETTER[face] + (f" {note}" if note else "")
                draw.text((r[0] + 2, r[1] + 1), letter, fill=(255, 255, 255, 230), font=small)
                if face == "north":  # biggest/front face carries the bone label
                    label = f"{bname}{ci if ci else ''}{mirror}"
                    draw.text((r[0] + 2, r[1] + scale), label, fill=(255, 255, 255, 255), font=font)

    if grid:
        g = ImageDraw.Draw(overlay)
        for tx in range(tw + 1):
            g.line([(tx * scale, 0), (tx * scale, H)], fill=(255, 255, 255, 18))
        for ty in range(th + 1):
            g.line([(0, ty * scale), (W, ty * scale)], fill=(255, 255, 255, 18))

    out = Image.alpha_composite(base, overlay)
    ImageDraw.Draw(out).text((2, H - scale - 2), f"{ident}  {tw}x{th}", fill=(200, 200, 200, 255), font=small)

    dest_dir = out_dir if out_dir else path.parent
    dest = dest_dir / (path.name.replace(".geo.json", "") + ".uvtemplate.png")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out.save(dest)

    for w in warnings:
        print(f"  WARN {path.name}: {w}", file=sys.stderr)
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help=".geo.json files or directories to scan")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--scale", type=int, default=8)
    ap.add_argument("--under", type=Path, default=None)
    ap.add_argument("--no-grid", action="store_true")
    ap.add_argument("--alpha", type=int, default=70)
    args = ap.parse_args()

    files = []
    for p in map(Path, args.paths):
        if p.is_dir():
            files.extend(sorted(p.glob("*.geo.json")))
        elif p.exists():
            files.append(p)
        else:
            sys.exit(f"not found: {p}")
    if not files:
        sys.exit("no .geo.json found")
    if args.under and len(files) != 1:
        sys.exit("--under requires exactly one input model")

    for f in files:
        dest = render(f, args.out, args.scale, args.under, not args.no_grid, args.alpha)
        print(f"{f.name} -> {dest}")


if __name__ == "__main__":
    main()
