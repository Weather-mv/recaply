"""Draw the Clipforge logo (blue tile, replay arrow, play triangle) as a Windows .ico."""
import math
import os
import sys

from PIL import Image, ImageDraw

S = 1024                                   # drawn big, scaled down for each icon size


def logo():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # gradient tile: #3984ff (top-left) to #0101db (bottom-right)
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    a, b = (0x39, 0x84, 0xff), (0x01, 0x01, 0xdb)
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S - 2)
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.28), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    k = S / 32                              # the SVG logo is drawn on a 32x32 grid
    w = int(2.6 * k)
    # replay arc: circle centre (16,16) r=8, open at the top right (SVG: M22.6 10.6 A8 8 0 1 0 24 16).
    # PIL measures degrees clockwise from 3 o'clock, so the arc runs 0 -> 321.
    box = (8 * k, 8 * k, 24 * k, 24 * k)
    d.arc(box, start=0, end=321, fill="white", width=w)
    for deg in (0, 321):                    # round line caps
        x, y = 16 + 8 * math.cos(math.radians(deg)), 16 + 8 * math.sin(math.radians(deg))
        d.ellipse((x * k - w / 2, y * k - w / 2, x * k + w / 2, y * k + w / 2), fill="white")
    # arrow head at the top right
    d.line([(24.4 * k, 6.8 * k), (24.4 * k, 11.4 * k), (19.8 * k, 11.4 * k)], fill="white", width=w, joint="curve")
    for x, y in ((24.4, 6.8), (24.4, 11.4), (19.8, 11.4)):
        r = w / 2
        d.ellipse((x * k - r, y * k - r, x * k + r, y * k + r), fill="white")
    # play triangle
    d.polygon([(14 * k, 12.3 * k), (14 * k, 19.7 * k), (19.8 * k, 16 * k)], fill="white")
    return img


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "recaply.ico")
    big = logo()
    big.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((256, 256), Image.LANCZOS).save(os.path.splitext(out)[0] + ".png")
    print("wrote", out)
