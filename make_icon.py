"""Draws the app icon (a music note on a microchip, in the app's pastel
palette) into assets/.

Run after changing the design:  python make_icon.py
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

OUT = Path(__file__).parent / "assets"
K = 2           # design in 1024 units, draw at 2048 for smooth edges
S = 1024 * K

LAVENDER, BLUSH = (205, 189, 242), (247, 220, 229)
INK, INK_LIGHT = (52, 48, 68), (78, 72, 98)
NOTE_TOP, NOTE_BOTTOM = (248, 244, 255), (178, 228, 205)
MINT_DEEP = (96, 170, 138)
PIN = (246, 236, 248)

CHIP = (232, 232, 792, 792)  # x0, y0, x1, y1 in design units


def u(v):
    return round(v * K)


def gradient(size, top, bottom, diagonal=False):
    """Smooth two-colour gradient, built small and scaled up."""
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126 if diagonal else y / 63
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom))
    return small.resize(size, Image.BICUBIC)


def tint(mask, color, alpha=1.0):
    """An RGBA layer of `color`, shaped by an L-mode mask."""
    layer = Image.new("RGBA", mask.size, color + (0,))
    layer.putalpha(mask.point(lambda v: round(v * alpha)))
    return layer


def rounded_mask(box, radius):
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle(tuple(u(v) for v in box), radius=u(radius), fill=255)
    return m


def circuit_traces(rng):
    """Traces leaving the top and bottom pins, bending 45 degrees and ending in pads."""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = CHIP
    pins = [x0 + 58 + i * (x1 - x0 - 116) / 7 for i in range(8)]
    sides = [((0, -1), (1, 0), lambda p: (p, y0 - 28)), ((0, 1), (1, 0), lambda p: (p, y1 + 28))]
    for (ox, oy), (px, py), start in sides:
        for i, p in enumerate(pins):
            if rng.random() < 0.2:
                continue
            s = -1 if i < 4 else 1  # fan away from the middle
            a = start(p)
            b = (a[0] + ox * rng.uniform(25, 70), a[1] + oy * rng.uniform(25, 70))
            run = rng.uniform(30, 90)
            c = (b[0] + (ox + px * s) * run, b[1] + (oy + py * s) * run)
            end = (c[0] + ox * 300, c[1] + oy * 300) if rng.random() < 0.6 else c
            pts = [tuple(u(v) for v in pt) for pt in (a, b, c, end)]
            d.line(pts, fill=(255, 255, 255, 150), width=u(7), joint="curve")
            if end == c:
                r = u(11)
                d.ellipse((pts[-1][0] - r, pts[-1][1] - r, pts[-1][0] + r, pts[-1][1] + r),
                          fill=(255, 255, 255, 190))
    return layer


def sound_waves():
    """Concentric arcs coming off the chip's left and right sides: the voice part."""
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = cy = 512
    width, span = 13, 30
    for radius, alpha in ((405, 200), (465, 140)):
        box = (u(cx - radius), u(cy - radius), u(cx + radius), u(cy + radius))
        for mid in (0, 180):  # right, left
            a0, a1 = mid - span, mid + span
            d.arc(box, start=a0, end=a1, fill=(255, 255, 255, alpha), width=u(width))
            for a in (a0, a1):  # round the ends
                ex = cx + (radius - width / 2) * math.cos(math.radians(a))
                ey = cy + (radius - width / 2) * math.sin(math.radians(a))
                r = u(width / 2)
                d.ellipse((u(ex) - r, u(ey) - r, u(ex) + r, u(ey) + r), fill=(255, 255, 255, alpha))
    return layer


def note_mask():
    """Eighth note: tilted head, stem, and a tapering curved flag."""
    m = Image.new("L", (S, S), 0)
    head = Image.new("L", (S, S), 0)
    cx, cy = 430, 650
    ImageDraw.Draw(head).ellipse((u(cx - 118), u(cy - 86), u(cx + 118), u(cy + 86)), fill=255)
    m = ImageChops.lighter(m, head.rotate(22, center=(u(cx), u(cy)), resample=Image.BICUBIC))
    d = ImageDraw.Draw(m)
    d.rounded_rectangle((u(492), u(290), u(550), u(650)), radius=u(14), fill=255)
    p0, p1, p2, p3 = (521, 305), (610, 330), (730, 405), (672, 578)
    for i in range(400):
        t = i / 399
        x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0]
        y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1]
        r = u(38 - 24 * t)
        d.ellipse((u(x) - r, u(y) - r, u(x) + r, u(y) + r), fill=255)
    return m


def draw(detail=True):
    rng = random.Random(7)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # Background tile
    tile = rounded_mask((0, 0, 1023, 1023), 230)
    img.paste(gradient((S, S), LAVENDER, BLUSH, diagonal=True), (0, 0), tile)
    if detail:
        traces = circuit_traces(rng)
        traces.alpha_composite(sound_waves())
        traces.putalpha(ImageChops.multiply(traces.getchannel("A"), tile))
        img.alpha_composite(traces)

    # Chip: shadow, pins, body, rim
    x0, y0, x1, y1 = CHIP
    shadow = rounded_mask((x0, y0 + 26, x1, y1 + 26), 70).filter(ImageFilter.GaussianBlur(u(30)))
    img.alpha_composite(tint(shadow, (60, 40, 90), 0.45))
    pins = Image.new("L", (S, S), 0)
    pd = ImageDraw.Draw(pins)
    for i in range(8):
        p = x0 + 58 + i * (x1 - x0 - 116) / 7
        for b in ((p - 12, y0 - 30, p + 12, y0 + 10), (p - 12, y1 - 10, p + 12, y1 + 30),
                  (x0 - 30, p - 12, x0 + 10, p + 12), (x1 - 10, p - 12, x1 + 30, p + 12)):
            pd.rounded_rectangle(tuple(u(v) for v in b), radius=u(7), fill=255)
    img.alpha_composite(tint(pins, PIN))
    body = rounded_mask(CHIP, 64)
    img.paste(gradient((S, S), INK_LIGHT, INK), (0, 0), body)
    rim = Image.new("L", (S, S), 0)
    ImageDraw.Draw(rim).rounded_rectangle((u(x0 + 8), u(y0 + 8), u(x1 - 8), u(y1 - 8)),
                                          radius=u(58), outline=255, width=u(4))
    img.alpha_composite(tint(rim, (255, 255, 255), 0.12))

    # Note
    mask = note_mask()
    note_fill = gradient((S, S), NOTE_TOP, NOTE_BOTTOM).convert("RGBA")
    note_fill.putalpha(mask)
    img.alpha_composite(note_fill)
    edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(u(5) | 1)))
    img.alpha_composite(tint(edge, MINT_DEEP, 0.8))
    return img


def main():
    OUT.mkdir(exist_ok=True)
    full = draw(detail=True)
    simple = draw(detail=False)  # fine traces turn to mush at tiny sizes
    full.resize((1024, 1024), Image.LANCZOS).save(OUT / "icon.png")
    # Every size Windows asks for at 100-250% scaling, so it never has to stretch one.
    big = [full.resize((s, s), Image.LANCZOS) for s in (256, 128, 96, 80, 72, 64, 60, 48)]
    small = [simple.resize((s, s), Image.LANCZOS).filter(
        ImageFilter.UnsharpMask(radius=0.8, percent=90, threshold=0))
        for s in (40, 36, 32, 30, 24, 20, 16)]
    big[0].save(OUT / "icon.ico", sizes=[im.size for im in big + small],
                append_images=big[1:] + small)
    print("wrote", OUT / "icon.png", "and", OUT / "icon.ico")


if __name__ == "__main__":
    main()
