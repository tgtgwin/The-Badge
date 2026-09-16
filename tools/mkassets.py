#!/usr/bin/env python3
"""PNG/SVG → LVGL 9 C arrays.

Baked here directly to avoid depending on pypng. Designated initialisers mean
the bitfield order does not matter.
  RGB565     an opaque background
  RGB565A8   anything needing alpha (hands and so on) — an A8 block follows the RGB565 one
  A8         single-colour icons. LVGL recolours them at draw time
"""
import os, sys, subprocess, math
from PIL import Image, ImageDraw

# 🚨 A home-server path used to be written in here, so it would not run at all
# on another machine (it caught us out on the company Windows PC, 09-09).
# Paths are taken relative to the repo and only what lives outside comes from an
# environment variable. Missing, and just that part is skipped — the tool does
# not die whole.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "main", "assets")
SHOTS = os.path.join(REPO, "shots")

# The clock dial originals live outside this repo (from the watchface-s5 work).
# Without them the clock assets are skipped.


def rgb565(px):
    r, g, b = px[0] >> 3, px[1] >> 2, px[2] >> 3
    return (r << 11) | (g << 5) | b


# ── the two sizes an app icon has ────────────────────────────
# ICON is the size they are **designed** at: every offset below — where the
# cursor sits on the mouse tile, how far the keyboard is pulled in, the 4x
# supersampling factor — is a number in this space, tuned by eye at this size.
#
# ICON_OUT is the size they are **emitted** at, and it must equal ICON_D in
# main/launcher.c.
#
# 🚨 Why two numbers instead of one. Changing ICON itself would move all those
#    hand-tuned offsets at once, because they are absolute pixels rather than
#    fractions of ICON — the cursor would end up off the edge of a smaller tile.
#    Designing at 120 and emitting at 90 keeps every offset where it was and
#    still hands the launcher exactly the pixels it draws.
#
# 🚨 Why this matters at all. The icons used to be baked at 120 and displayed at
#    92, so LVGL rescaled every one of them at run time through its transform
#    path — a 2x2 bilinear with no area averaging, one pixel at a time. That is
#    where the graininess came from, and it was never the artwork. Emitting at
#    the size they are drawn lets the launcher ask for 1:1 and get no resampling.
ICON = 120
ICON_OUT = 90
assert ICON_OUT <= ICON, "an icon can only be shrunk, not enlarged, on the way out"

def emit(name, img, fmt, dither=False):
    """fmt: 'RGB565' | 'RGB565A8' | 'A8'

    dither=True spreads the error RGB565 truncation leaves behind into the
    neighbouring pixels (Floyd–Steinberg).

    🚨 This does the opposite of what was here before. That version added a
    fixed Bayer offset to every pixel whether or not anything had been lost, so
    a flat area — where the value asked for is representable and the error is
    zero — got a sprinkle of noise and nothing else. On a 466 px screen that
    reads as exactly the grain it was supposed to remove. Offsetting by the
    real quantisation error leaves flat areas flat and shades only gradients.

    🚨 Off unless a caller asks, and it should stay that way until an A/B in the
    simulator settles it: it costs a float pass over every pixel and which way
    round it helps is not obvious by eye."""
    w, h = img.size
    body = bytearray()
    if fmt == "A8":
        a = img.convert("RGBA").split()[3]
        body += a.tobytes()
        stride, cf = w, "LV_COLOR_FORMAT_A8"
    else:
        rgba = img.convert("RGBA")
        px = rgba.load()
        # Error carried into the pixel to the right, and into the row below.
        # Indexed by x + 1 so the row below can reach x - 1 without a branch.
        cur = [[0.0, 0.0, 0.0] for _ in range(w + 2)]
        for y in range(h):
            nxt = [[0.0, 0.0, 0.0] for _ in range(w + 2)]
            for x in range(w):
                p = px[x, y]
                if dither:
                    e = cur[x + 1]
                    r, g, b = p[0] + e[0], p[1] + e[1], p[2] + e[2]
                    r = 0.0 if r < 0.0 else (255.0 if r > 255.0 else r)
                    g = 0.0 if g < 0.0 else (255.0 if g > 255.0 else g)
                    b = 0.0 if b < 0.0 else (255.0 if b > 255.0 else b)
                    v = rgb565((int(r), int(g), int(b)))
                    # 🚨 The error is against what the panel will show, not
                    #    against the truncated integer. 5 bits expanded back to
                    #    8 is not a multiply by 255/31 — it is (v5 << 3) | (v5 >> 2),
                    #    and using the wrong one leaves a systematic drift that
                    #    the diffusion then faithfully accumulates.
                    q5r = (v >> 11) & 0x1F
                    q6g = (v >>  5) & 0x3F
                    q5b =  v        & 0x1F
                    dr = r - ((q5r << 3) | (q5r >> 2))
                    dg = g - ((q6g << 2) | (q6g >> 4))
                    db = b - ((q5b << 3) | (q5b >> 2))
                    cur[x + 2][0] += dr * 7.0 / 16.0
                    cur[x + 2][1] += dg * 7.0 / 16.0
                    cur[x + 2][2] += db * 7.0 / 16.0
                    nxt[x    ][0] += dr * 3.0 / 16.0
                    nxt[x    ][1] += dg * 3.0 / 16.0
                    nxt[x    ][2] += db * 3.0 / 16.0
                    nxt[x + 1][0] += dr * 5.0 / 16.0
                    nxt[x + 1][1] += dg * 5.0 / 16.0
                    nxt[x + 1][2] += db * 5.0 / 16.0
                    nxt[x + 2][0] += dr      / 16.0
                    nxt[x + 2][1] += dg      / 16.0
                    nxt[x + 2][2] += db      / 16.0
                else:
                    v = rgb565(p)
                body += bytes((v & 0xFF, v >> 8))       # little-endian
            cur = nxt
        stride = w * 2
        cf = "LV_COLOR_FORMAT_RGB565"
        if fmt == "RGB565A8":
            body += rgba.split()[3].tobytes()
            cf = "LV_COLOR_FORMAT_RGB565A8"

    lines = [f"/* Generated by tools/mkassets.py. Do not edit by hand. */",
             '#include "lvgl.h"', "",
             f"static const uint8_t {name}_map[] = {{"]
    for i in range(0, len(body), 16):
        lines.append("    " + "".join("0x%02X," % b for b in body[i:i + 16]))
    lines.append("};")
    lines.append(f"""
const lv_image_dsc_t {name} = {{
    .header = {{
        .magic  = LV_IMAGE_HEADER_MAGIC,
        .cf     = {cf},
        .flags  = 0,
        .w      = {w},
        .h      = {h},
        .stride = {stride},
    }},
    .data_size = sizeof({name}_map),
    .data      = {name}_map,
}};""")
    path = os.path.join(OUT, f"{name}.c")
    open(path, "w").write("\n".join(lines) + "\n")
    print(f"{name:14} {w}x{h:<4} {fmt:9} {len(body)//1024:5}KB")
    return w, h


def mask_at(img):
    """Masks an icon to a circle at whatever size it was drawn at.

    🚨 At the drawn size, not at ICON. Trimming to ICON first and masking there
       would put two reductions in the way where one will do, and the second of
       them is the small one — see emit_icon for why that is the one that hurts.
    """
    img.putalpha(circle_mask(img.size[0]))
    return img


def emit_icon(name, img, fmt="RGB565A8"):
    """An app icon, emitted at ICON_OUT, from whatever size it was drawn at.

    🚨 The filter depends on how far it is being reduced, and neither choice is
       cosmetic.
       LANCZOS has negative lobes. On a thin bright ring over a light face it
       overshoots on the way in and undershoots on the way out, so the rim comes
       out beaded rather than continuous — which is exactly what the clock icon
       was doing, and what "jagged" turns out to mean on a 1.75 inch panel. BOX
       is a plain area average: no ringing, and at a large reduction it is also
       simply the right answer.
       But at a small reduction BOX has almost no support left and behaves like
       a nearest neighbour, aliasing in its own way. So: BOX when there is a lot
       to average, LANCZOS when there is not.

    🚨 And it happens ONCE. The callers used to shrink to ICON themselves and
       leave this to shrink ICON to ICON_OUT, which put a second, small,
       aliasing-prone step after a clean one for no reason at all. They now hand
       over the supersampled image and let this be the only resize — which is why
       it takes any size instead of assuming ICON.
    """
    src = img.size[0]
    if src != ICON_OUT:
        img = img.resize((ICON_OUT, ICON_OUT),
                         Image.BOX if src >= ICON_OUT * 2 else Image.LANCZOS)
    return emit(name, img, fmt)


# The mouse pointer, on the same 100x100 grid tools/art/cursor.svg drew it on.
#
# 🚨 Drawn here rather than rendered from the SVG. Rendering SVG means shelling
#    out to rsvg-convert, which is a homebrew package on a Mac and a distro
#    package everywhere else — a build dependency, for eight numbers. This tool
#    already bakes its own PNGs "to avoid depending on pypng"; the same argument
#    applies with more force here, and it means the icons can be rebuilt on a
#    machine that has nothing but Python and Pillow.
CURSOR_PATH = [(30, 18), (30, 78), (44, 64), (54, 86), (66, 80), (56, 59), (76, 57)]
CURSOR_FILL = (0xFF, 0xFF, 0xFF)
CURSOR_EDGE = (0x11, 0x22, 0x33)


def cursor_art(size):
    """The pointer, `size` px square, with smooth edges."""
    SS = 8
    n = size * SS
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    k = n / 100.0
    pts = [(x * k, y * k) for x, y in CURSOR_PATH]
    wdt = max(2, int(round(3.0 * k)))     # the SVG's stroke-width, in this scale
    d.polygon(pts, fill=CURSOR_FILL + (255,), outline=CURSOR_EDGE + (255,), width=wdt)
    return im.resize((size, size), Image.LANCZOS)


def circle_mask(size):
    m = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def disc(color):
    """A disc with a smooth edge"""
    im = Image.new("RGBA", (ICON, ICON), color + (255,))
    im.putalpha(circle_mask(ICON))
    return im


def clock_icon():
    """A plain analog clock, drawn four times over and shrunk down."""
    SS = 8
    n = ICON * SS
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pad = 2 * SS
    d.ellipse([pad, pad, n - pad, n - pad], fill="#F2F0EA",
              outline="#C9C5BA", width=3 * SS)

    c = n / 2
    for i in range(12):
        a = math.radians(i * 30 - 90)
        r0 = c - (12 if i % 3 == 0 else 9) * SS
        r1 = c - 20 * SS
        d.line([c + math.cos(a) * r0, c + math.sin(a) * r0,
                c + math.cos(a) * r1, c + math.sin(a) * r1],
               fill="#1B1D22" if i % 3 == 0 else "#8A8780",
               width=(3 if i % 3 == 0 else 2) * SS)

    def hand(deg, length, width, colour):
        r = math.radians(deg - 90)
        # A short tail past the centre is what makes a hand read as a hand.
        d.line([c - math.cos(r) * length * 0.18, c - math.sin(r) * length * 0.18,
                c + math.cos(r) * length,        c + math.sin(r) * length],
               fill=colour, width=width)

    hand(10 * 30 + 9 * 0.5, c * 0.50, 6 * SS, "#1B1D22")   # hour
    hand(9 * 6,             c * 0.72, 4 * SS, "#1B1D22")   # minute
    hand(30 * 6,            c * 0.76, 2 * SS, "#C8453A")   # second
    d.ellipse([c - 4 * SS, c - 4 * SS, c + 4 * SS, c + 4 * SS], fill="#1B1D22")

    # 🚨 Handed over at 960 and masked here. The single reduction to ICON_OUT
    #    happens in emit_icon; see the note there about why one is better than two.
    return mask_at(im)


def make_app_icons():
    """An app icon reads better as a shrunken picture of the app itself than as a glyph."""
    # Mouse — the trail a cursor left. It fades toward the back.
    m = disc((0x4E, 0x8C, 0xF5))
    cur = cursor_art(54)
    # A trail running outside the circle gets clipped and looks messy. It is drawn inward.
    ghosts = [(-24, -24, 55, 40), (-16, -16, 95, 46), (-8, -8, 150, 50)]
    for dx, dy, a, sz in ghosts:
        g = cursor_art(sz)
        g.putalpha(g.split()[3].point(lambda v: v * a // 255))
        m.alpha_composite(g, (48 + dx, 46 + dy))
    m.alpha_composite(cur, (48, 46))
    emit_icon("app_icon_mouse", m, "RGB565A8")

    # Clock. Drawn here rather than shrunk from a screenshot: the app's own
    # face is a digital panel, and a tiny one reads as a smudge at 120 px.
    # Hands sit at 10:09:30 for the reason watch advertising does it — the two
    # hands make a symmetric V and leave the middle of the dial clear.
    emit_icon("app_icon_clock", clock_icon(), "RGB565A8")

    emit("icon_gear", gear_icon(40), "A8")

    # Keys — the keyboard pulled right in, so only a few caps show
    S4 = ICON * 4
    k = Image.new("RGBA", (S4, S4), (0x15, 0x18, 0x20, 255))
    dk = ImageDraw.Draw(k)
    cap, gap = S4 * 0.255, S4 * 0.045
    cols, rows_n = 3, 2
    tw = cols * cap + (cols - 1) * gap
    th = rows_n * cap + (rows_n - 1) * gap
    ox, oy = (S4 - tw) / 2, (S4 - th) / 2
    for r in range(rows_n):
        for c in range(cols):
            x = ox + c * (cap + gap)
            y = oy + r * (cap + gap)
            hot = (r == 1 and c == 1)                 # only the middle-bottom cap is highlighted
            top  = (0x8A, 0xB4, 0xF8) if hot else (0xDF, 0xE4, 0xEA)
            side = (0x4E, 0x7C, 0xC0) if hot else (0x9A, 0xA2, 0xAE)
            rad = S4 * 0.035
            # A slightly thicker body underneath first, so it reads as solid
            dk.rounded_rectangle((x, y + S4 * 0.018, x + cap, y + cap), radius=rad, fill=side + (255,))
            dk.rounded_rectangle((x, y, x + cap, y + cap - S4 * 0.022), radius=rad, fill=top + (255,))
    emit_icon("app_icon_keys", mask_at(k), "RGB565A8")

    # The typer — three dots (a masked password)
    ty = disc((0x5A, 0x6E, 0x86))
    dt = ImageDraw.Draw(ty)
    for i in range(3):
        cx = ICON / 2 + (i - 1) * 24
        dt.ellipse((cx - 8, ICON / 2 - 8, cx + 8, ICON / 2 + 8), fill=(255, 255, 255, 255))
    emit_icon("app_icon_type", ty, "RGB565A8")

    # The presenter — one slide and an arrow
    pr = disc((0x3E, 0x8E, 0x78))
    dp = ImageDraw.Draw(pr)
    dp.rounded_rectangle((26, 34, 82, 74), radius=5, outline=(255, 255, 255, 255), width=5)
    dp.polygon([(64, 88), (98, 88), (81, 104)], fill=(255, 255, 255, 255))
    emit_icon("app_icon_present", pr, "RGB565A8")

    # The calculator — an equals and a plus
    ca = disc((0xC8, 0x8E, 0x33))
    dc2 = ImageDraw.Draw(ca)
    dc2.rounded_rectangle((30, 44, 90, 52), radius=4, fill=(255, 255, 255, 255))
    dc2.rounded_rectangle((30, 66, 90, 74), radius=4, fill=(255, 255, 255, 255))
    emit_icon("app_icon_calc", ca, "RGB565A8")

    # Games — the game that is in there now (breakout), as it is. Bricks, ball, curved paddle.
    S4 = ICON * 4
    gm = Image.new("RGBA", (S4, S4), (0x12, 0x16, 0x20, 255))
    dg = ImageDraw.Draw(gm)
    rows = [(0x7F, 0xB0, 0xFF), (0x5B, 0xD4, 0x8A), (0xE0, 0xA3, 0x3A)]
    bw, bh, gap = S4 * 0.20, S4 * 0.072, S4 * 0.022
    for r, col in enumerate(rows):
        n = 3 if r == 0 else 4
        total = n * bw + (n - 1) * gap
        x = (S4 - total) / 2
        y = S4 * 0.17 + r * (bh + gap)
        for k in range(n):
            dg.rounded_rectangle((x, y, x + bw, y + bh), radius=S4 * 0.014, fill=col + (255,))
            x += bw + gap
    # The ball
    cx, cy, rr = S4 * 0.5, S4 * 0.62, S4 * 0.05
    dg.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=(255, 255, 255, 255))
    # The curved paddle — an arc across the bottom
    pad = S4 * 0.13
    dg.arc((pad, pad, S4 - pad, S4 - pad), 58, 122,
           fill=(255, 255, 255, 255), width=int(S4 * 0.055))
    emit_icon("app_icon_games", mask_at(gm), "RGB565A8")


    # Meeting — a microphone. The phone does the recording, but a microphone is what the hand looks for.
    S5 = ICON * 4
    mt = Image.new("RGBA", (S5, S5), (0x18, 0x10, 0x14, 255))
    dm = ImageDraw.Draw(mt)
    # The capsule
    cw, ch = S5 * 0.20, S5 * 0.38
    cx0, cy0 = (S5 - cw) / 2, S5 * 0.17
    dm.rounded_rectangle((cx0, cy0, cx0 + cw, cy0 + ch), radius=cw / 2,
                         fill=(0xFF, 0x5B, 0x5B, 255))
    # The cradle arc and the stem
    pad = S5 * 0.27
    dm.arc((pad, S5 * 0.24, S5 - pad, S5 * 0.74), 20, 160,
           fill=(255, 255, 255, 255), width=int(S5 * 0.045))
    dm.rounded_rectangle((S5 * 0.485, S5 * 0.68, S5 * 0.515, S5 * 0.82),
                         radius=S5 * 0.015, fill=(255, 255, 255, 255))
    emit_icon("app_icon_meet", mask_at(mt), "RGB565A8")

    # Timer — the shrinking ring itself
    S = ICON * 4
    tm = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    dr = ImageDraw.Draw(tm)
    dr.ellipse((0, 0, S - 1, S - 1), fill=(0x14, 0x17, 0x1D, 255))
    pad, wdt = S * 0.17, int(S * 0.075)
    dr.arc((pad, pad, S - pad, S - pad), -90, 270, fill=(0x24, 0x26, 0x2C, 255), width=wdt)
    dr.arc((pad, pad, S - pad, S - pad), -90, 155, fill=(0x7F, 0xB0, 0xFF, 255), width=wdt)
    emit_icon("app_icon_timer", mask_at(tm), "RGB565A8")


def gear_icon(size):
    """Drawing the teeth as an SVG path never holds its shape. There are eight of them, worked out and drawn directly."""
    S = size * 4
    im = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    c = S / 2
    r_body, r_tooth, r_hole = S * 0.30, S * 0.42, S * 0.135
    half = math.radians(11)

    for i in range(8):
        a = math.radians(i * 45)
        pts = []
        for sign, rad in ((-1, r_body * 0.98), (1, r_body * 0.98)):
            pass
        # One trapezoid tooth: wide at the inside, slightly narrower at the outside
        for ang, rad in ((a - half * 1.35, r_body * 0.95), (a - half, r_tooth),
                         (a + half, r_tooth), (a + half * 1.35, r_body * 0.95)):
            pts.append((c + math.sin(ang) * rad, c - math.cos(ang) * rad))
        d.polygon(pts, fill=(255, 255, 255, 255))

    d.ellipse((c - r_body, c - r_body, c + r_body, c + r_body), fill=(255, 255, 255, 255))
    d.ellipse((c - r_hole, c - r_hole, c + r_hole, c + r_hole), fill=(255, 255, 255, 0))
    return im.resize((size, size), Image.LANCZOS)


def main():
    """Bakes what this repo actually ships: the app icons and the gear glyph.

    It used to bake two watch dials and a wallpaper as well. Those were other
    people's artwork, so they are gone — the clock is drawn in code now
    (main/lcdface.c) and the home background is a gradient."""
    os.makedirs(OUT, exist_ok=True)
    defs = []

    make_app_icons()

    hdr = ["/* generated by tools/mkassets.py */", "#pragma once",
           '#include "lvgl.h"', ""]
    for n in ("app_icon_mouse", "app_icon_clock", "app_icon_timer",
              "app_icon_keys", "app_icon_type", "app_icon_present",
              "app_icon_calc", "app_icon_games", "app_icon_meet",
              "icon_gear"):
        hdr.append(f"extern const lv_image_dsc_t {n};")
    hdr += [""] + defs + [""]
    open(os.path.join(OUT, "assets.h"), "w").write("\n".join(hdr) + "\n")
    print("assets.h written")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_cmake.py")], check=True)



if __name__ == "__main__":
    main()

