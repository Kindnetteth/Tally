"""Draw Tally's icons: the app icon in each colour scheme and the menu bar template.

    python packaging/make_icons.py

Writes tally/assets/icon-<scheme>.png for every scheme the app offers,
icon.png (the default scheme), menubar.png, plus packaging/Tally.icns
(macOS only, needs iconutil) and packaging/Tally.ico. Needs Pillow.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "tally" / "assets"
SS = 2  # supersampling for smooth edges

# Keep in sync with ACCENTS in tally/web/style.css and SCHEMES in tally/desktop.py
SCHEMES = {
    "orchid": ("#ff5fa2", "#8b5cf6"),
    "violet": ("#b06bff", "#4f6bff"),
    "ocean": ("#38bdf8", "#2563eb"),
    "mint": ("#34d399", "#0ea5a4"),
    "sunset": ("#ffb347", "#ff4f6d"),
    "graphite": ("#8e8e99", "#3b3b45"),
}
DEFAULT = "orchid"


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def gradient(size, start, end):
    """Diagonal gradient from the top left corner to the bottom right."""
    small = Image.new("RGB", (256, 256))
    px = small.load()
    a, b = rgb(start), rgb(end)
    for y in range(256):
        for x in range(256):
            t = (x + y) / 510
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return small.resize((size, size), Image.BICUBIC)


def marks_mask(size, scale, ox, oy, width):
    """The tally marks (four strokes and a slash) on a 32-unit grid, as an alpha mask."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)

    def line(x1, y1, x2, y2):
        p1, p2 = (ox + x1 * scale, oy + y1 * scale), (ox + x2 * scale, oy + y2 * scale)
        d.line([p1, p2], fill=255, width=round(width * scale))
        r = width * scale / 2
        for x, y in (p1, p2):
            d.ellipse([x - r, y - r, x + r, y + r], fill=255)
    for x in (9, 14, 19, 24):
        line(x, 8, x, 24)
    line(6, 20, 27, 12)
    return mask


def app_icon(start, end, size=1024, dark_tile=False):
    big = size * SS
    # macOS icon grid: 824px tile centered in 1024 with ~22% corner radius
    pad = round(big * 100 / 1024)
    tile = big - 2 * pad
    shape = Image.new("L", (big, big), 0)
    ImageDraw.Draw(shape).rounded_rectangle([pad, pad, pad + tile, pad + tile], radius=round(tile * 0.225),
                                            fill=255)

    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    # Soft drop shadow under the tile
    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    shadow.putalpha(shape.point(lambda v: v * 0.35))
    shadow = ImageChops.offset(shadow, 0, round(big * 0.012)).filter(ImageFilter.GaussianBlur(big * 0.012))
    img.alpha_composite(shadow)

    if dark_tile:
        face = gradient(big, "#24242e", "#101016").convert("RGBA")
    else:
        face = gradient(big, start, end).convert("RGBA")
    # Gentle light from the top
    glow = Image.linear_gradient("L").resize((big, big)).point(lambda v: max(0, 70 - v * 0.55))
    face = Image.composite(Image.new("RGBA", (big, big), (255, 255, 255, 255)), face, glow)
    face.putalpha(shape)
    img.alpha_composite(face)

    marks = marks_mask(big, tile / 32, pad, pad, 2.6)
    # Marks cast a faint shadow so they sit on the tile, like IINA's play symbol
    mark_shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    mark_shadow.putalpha(ImageChops.offset(marks, 0, round(big * 0.008)).point(lambda v: v * 0.28))
    mark_shadow = mark_shadow.filter(ImageFilter.GaussianBlur(big * 0.01))
    img.alpha_composite(mark_shadow)
    if dark_tile:
        ink = gradient(big, start, end).convert("RGBA")
    else:
        ink = Image.new("RGBA", (big, big), (255, 255, 255, 255))
    ink.putalpha(marks)
    img.alpha_composite(ink)
    # Thin inner edge highlight
    edge = shape.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(1.2 * SS))
    rim = Image.new("RGBA", (big, big), (255, 255, 255, 0))
    rim.putalpha(edge.point(lambda v: min(255, v) * 0.22))
    img.alpha_composite(rim)
    return img.resize((size, size), Image.LANCZOS)


def menubar_icon():
    # 22x18 points at 2x; black on transparent so macOS can tint it
    w, h = 44 * 4, 36 * 4
    scale = h / 22
    mask = marks_mask(max(w, h), scale, (w - 32 * scale) / 2 + 0.5 * scale, -5 * scale, 2.6).crop((0, 0, w, h))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.putalpha(mask)
    return img.resize((44, 36), Image.LANCZOS)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    icons = {}
    for name, (start, end) in SCHEMES.items():
        icons[name] = app_icon(start, end)
        icons[name].resize((512, 512), Image.LANCZOS).save(ASSETS / f"icon-{name}.png", optimize=True)
    app_icon(*SCHEMES["orchid"], dark_tile=True).resize((512, 512), Image.LANCZOS).save(
        ASSETS / "icon-midnight.png", optimize=True)
    icon = icons[DEFAULT]
    icon.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon.png", optimize=True)
    menubar_icon().save(ASSETS / "menubar.png")
    icon.save(ROOT / "packaging" / "Tally.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin" and shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "Tally.iconset"
            iconset.mkdir()
            for s in (16, 32, 128, 256, 512):
                icon.resize((s, s), Image.LANCZOS).save(iconset / f"icon_{s}x{s}.png")
                icon.resize((s * 2, s * 2), Image.LANCZOS).save(iconset / f"icon_{s}x{s}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o",
                            str(ROOT / "packaging" / "Tally.icns")], check=True)
    print("icons written")


if __name__ == "__main__":
    main()
