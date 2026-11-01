"""Draw the background of the installer window (dist/Tally.dmg).

    python packaging/make_dmg_background.py

Writes packaging/dmg-background.tiff with 1x and 2x versions for Retina
screens. The icon spots match ICONS in packaging/build.py. Needs Pillow.
"""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 640, 400
ICONS = {"app": (170, 190), "apps": (470, 190)}  # centres, in window points


def font(size, scale):
    for name in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size * scale)
        except OSError:
            pass
    return ImageFont.load_default()


def draw(scale):
    w, h = WIDTH * scale, HEIGHT * scale
    img = Image.new("RGB", (w, h), (250, 249, 252))
    # A soft orchid wash from the top, like the app icon's colours
    glow = Image.new("RGB", (w, h), (250, 249, 252))
    g = ImageDraw.Draw(glow)
    g.ellipse([-w * 0.25, -h * 1.0, w * 0.6, h * 0.45], fill=(255, 200, 225))
    g.ellipse([w * 0.4, -h * 1.0, w * 1.25, h * 0.4], fill=(210, 200, 255))
    glow = glow.filter(ImageFilter.GaussianBlur(90 * scale))
    img = Image.blend(img, glow, 0.8)
    d = ImageDraw.Draw(img)

    # Arrow from the app to Applications, in the icon's pink-to-purple
    (x1, y), (x2, _) = ICONS["app"], ICONS["apps"]
    a, b, y = (x1 + 85) * scale, (x2 - 85) * scale, (y - 8) * scale
    shaft = Image.new("RGB", (w, h))
    sd = ImageDraw.Draw(shaft)
    for x in range(int(a), int(b) + 1):
        t = (x - a) / max(1, b - a)
        sd.line([(x, 0), (x, h)], fill=(round(255 - 116 * t), round(95 - 3 * t), round(162 + 84 * t)))
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([a, y - 2.5 * scale, b - 14 * scale, y + 2.5 * scale], radius=2.5 * scale, fill=255)
    md.polygon([(b, y), (b - 20 * scale, y - 12 * scale), (b - 20 * scale, y + 12 * scale)], fill=255)
    img.paste(shaft, (0, 0), mask)

    title = "Drag Tally into Applications"
    f = font(19, scale)
    tw = d.textlength(title, font=f)
    d.text(((w - tw) / 2, 312 * scale), title, font=f, fill=(30, 28, 38))
    sub = "Then open it from Applications. It lives in your menu bar."
    f2 = font(13, scale)
    sw = d.textlength(sub, font=f2)
    d.text(((w - sw) / 2, 342 * scale), sub, font=f2, fill=(110, 106, 125))
    return img


def main():
    with tempfile.TemporaryDirectory() as tmp:
        one, two = Path(tmp) / "bg.png", Path(tmp) / "bg@2x.png"
        draw(1).save(one)
        draw(2).save(two)
        subprocess.run(["tiffutil", "-cathidpicheck", str(one), str(two), "-out",
                        str(ROOT / "packaging" / "dmg-background.tiff")], check=True, capture_output=True)
        draw(2).save(ROOT / "packaging" / "dmg-background-preview.png")
    print("dmg background written")


if __name__ == "__main__":
    main()
