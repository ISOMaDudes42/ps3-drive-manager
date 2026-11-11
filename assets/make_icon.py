"""Draws the PS3 Drive Manager logo and writes icon.png (1024 px) and icon.ico (16-256 px).

Run with:  python assets/make_icon.py
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

S = 1024           # final size
SS = 2             # supersampling factor for smooth edges
W = S * SS
OUT = os.path.dirname(os.path.abspath(__file__))


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def rounded_mask(size, radius):
    m = Image.new('L', (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    return m


def background():
    top, bottom = (37, 99, 235), (10, 14, 30)
    grad = Image.new('RGB', (1, W))
    for y in range(W):
        grad.putpixel((0, y), lerp(top, bottom, (y / W) ** 0.8))
    bg = grad.resize((W, W)).convert('RGBA')
    # soft flowing waves across the lower half
    waves = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(waves)
    for k, (amp, phase, alpha, width) in enumerate(((60, 0.0, 70, 10), (80, 1.4, 45, 7), (45, 2.6, 35, 5))):
        pts = [(x, W * 0.72 + k * 40 * SS + amp * SS * math.sin(x / W * 2.2 * math.pi + phase))
               for x in range(-20, W + 20, 8)]
        d.line(pts, fill=(255, 255, 255, alpha), width=width * SS, joint='curve')
    bg.alpha_composite(waves.filter(ImageFilter.GaussianBlur(2 * SS)))
    tile = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    tile.paste(bg, (0, 0), rounded_mask(W, 220 * SS))
    return tile


def disc(layer):
    cx, cy, r = 470 * SS, 450 * SS, 300 * SS
    d = ImageDraw.Draw(layer)
    # shadow
    sh = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((cx - r + 18 * SS, cy - r + 28 * SS, cx + r + 18 * SS, cy + r + 28 * SS),
                               fill=(0, 0, 0, 120))
    layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(24 * SS)))
    # disc body with concentric shading
    for i in range(60):
        t = i / 59
        rr = r * (1 - t * 0.62)
        col = lerp((214, 222, 235), (248, 250, 252), math.sin(t * math.pi * 3) * 0.5 + 0.5)
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col + (255,))
    # iridescent sheen wedge
    sheen = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.pieslice((cx - r, cy - r, cx + r, cy + r), 200, 250, fill=(147, 197, 253, 110))
    sd.pieslice((cx - r, cy - r, cx + r, cy + r), 20, 60, fill=(196, 181, 253, 90))
    mask = Image.new('L', (W, W), 0)
    ImageDraw.Draw(mask).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
    sheen.putalpha(Image.composite(sheen.getchannel('A'), Image.new('L', (W, W), 0), mask))
    layer.alpha_composite(sheen.filter(ImageFilter.GaussianBlur(10 * SS)))
    # rims and hub
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(148, 163, 184, 255), width=6 * SS)
    hub = 112 * SS
    d.ellipse((cx - hub, cy - hub, cx + hub, cy + hub), fill=(203, 213, 225, 255),
              outline=(148, 163, 184, 255), width=5 * SS)
    hole = 48 * SS
    d.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), fill=(15, 23, 42, 255))


def usb_stick(layer):
    d = ImageDraw.Draw(layer)
    x0, x1 = 610 * SS, 850 * SS
    body_top, body_bot = 560 * SS, 880 * SS
    plug_x0, plug_x1 = 655 * SS, 805 * SS
    plug_top = 420 * SS
    outline = 22 * SS  # dark gap so the stick reads clearly over the disc
    d.rounded_rectangle((x0 - outline, plug_top - outline, x1 + outline, body_bot + outline), 70 * SS,
                        fill=(10, 14, 30, 255))
    # metal connector
    d.rounded_rectangle((plug_x0, plug_top, plug_x1, body_top + 30 * SS), 18 * SS, fill=(203, 213, 225, 255))
    for px in (695 * SS, 745 * SS):
        d.rectangle((px, plug_top + 40 * SS, px + 26 * SS, plug_top + 76 * SS), fill=(71, 85, 105, 255))
    # body with gradient
    body = Image.new('RGBA', (x1 - x0, body_bot - body_top))
    for y in range(body.height):
        c = lerp((96, 165, 250), (29, 78, 216), y / body.height)
        ImageDraw.Draw(body).line((0, y, body.width, y), fill=c + (255,))
    m = Image.new('L', body.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, body.width - 1, body.height - 1), 48 * SS, fill=255)
    layer.paste(body, (x0, body_top), m)
    # highlight and a "play" arrow on the stick (drawn on an overlay so they blend)
    over = Image.new('RGBA', (W, W), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    od.rounded_rectangle((x0 + 26 * SS, body_top + 24 * SS, x0 + 46 * SS, body_bot - 60 * SS), 10 * SS,
                         fill=(255, 255, 255, 70))
    cx, cy, s = (x0 + x1) // 2 + 10 * SS, (body_top + body_bot) // 2 + 10 * SS, 62 * SS
    od.polygon([(cx - s * 0.7, cy - s), (cx - s * 0.7, cy + s), (cx + s, cy)], fill=(255, 255, 255, 245))
    layer.alpha_composite(over)


def main():
    img = background()
    disc(img)
    usb_stick(img)
    img = img.resize((S, S), Image.LANCZOS)
    img.save(os.path.join(OUT, 'icon.png'))
    sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]
    img.save(os.path.join(OUT, 'icon.ico'), sizes=sizes)
    print('wrote', os.path.join(OUT, 'icon.png'), 'and icon.ico')


if __name__ == '__main__':
    main()
