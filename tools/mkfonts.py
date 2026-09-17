#!/usr/bin/env python3
"""Bakes the Chinese fonts the interface needs.

The interface is in Chinese and LVGL's Montserrat has no CJK glyphs at all, so
without this the labels come out as blank boxes. This makes one font per size
used, from a system font, carrying only the characters the sources contain.

  python3 tools/mkfonts.py            rebuild whatever is out of date
  python3 tools/mkfonts.py --force    rebuild regardless
  python3 tools/mkfonts.py --list     show the characters and sizes, change nothing

🚨 Four decisions here are not the obvious ones, and each was made for a reason
   worth not rediscovering:

   1. **The character set is read out of the sources, not written down.** A
      hand-kept list is right on the day it is written and quietly wrong from
      the next string change onwards, and the failure is one blank box in one
      label, on a device, found by a person. Scanning means that adding a string
      to a .c file is all it takes.

   2. **Every size gets the same characters.** The plan was a subset per size on
      the grounds that a 26 px font draws a couple of words and nothing else.
      The measurement made that beside the point: the whole set at every size is
      about 34 KB against roughly 2.4 MB of headroom. Splitting it would save
      maybe 20 KB and cost a size-to-characters table that has to be kept in
      step with the code — and that has to be rebuilt every time a label moves
      between sizes, which happens for reasons that have nothing to do with
      fonts.

   3. **No Latin in the Chinese fonts.** They fall back to Montserrat, so
      digits, spaces and the F-keys keep the letterforms the rest of the
      interface already uses. Baking Latin into the Chinese faces would put two
      different sets of digits on the same screen.

   4. **Uncompressed.** lv_font_conv compresses by default and LVGL then
      decompresses a glyph on every draw. For a badge whose power budget is the
      point, trading a glyph-sized decompression per label for 34 KB of flash is
      the wrong way round.
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "main", "fonts")

# 🚨 The source, and why this one. Hiragino Sans GB ships a regular and a bold
#    in the same collection, so the home labels' bold is a real face rather than
#    a synthesised one. macOS only; elsewhere point SRC_TTC at whatever CJK font
#    is installed (Noto Sans SC works).
SRC_TTC = "/System/Library/Fonts/Hiragino Sans GB.ttc"
REGULAR_IDX = 0        # Hiragino Sans GB W3
BOLD_IDX = 2           # Hiragino Sans GB W6

# 🚨 Every size the interface passes Chinese through. 32/40/48 are deliberately
#    absent: what is drawn that large is digits, symbols and the words REC and
#    OK, and those stay Latin. tools/regress.sh fails if a label at one of those
#    sizes ever grows a Chinese word.
SIZES = [14, 16, 18, 20, 24, 26]
# 🚨 The home ring's labels are the only place that wants a heavier face, and
#    the size has to stay the one launcher.c asks for — at 26 px a Chinese
#    glyph is mostly one-pixel strokes, and a regular weight at that size reads
#    as grey rather than as a character. tools/regress.sh checks the two agree.
BOLD_SIZES = [18]

# 🚨 Bits per pixel, and why it is not one number.
#    A Chinese glyph at 14-20 px has strokes one pixel wide, and how good they
#    look comes down to how many levels of grey a stroke's edge can land on.
#    Four bits is sixteen levels, which is not enough: the thin strokes wash out
#    into the background and a label reads as a smudge rather than as text. Eight
#    bits is two hundred and fifty-six, and the edge lands where it should.
#    At 24 and 26 px the strokes are two pixels and thicker, sixteen levels is
#    plenty, and those two are the largest files by a wide margin — so they stay
#    at four. This is the whole reason the setting is per size.
BPP_SMALL = 8
BPP_LARGE = 4
BPP_SPLIT = 20          # sizes up to and including this get BPP_SMALL


def bpp_for(size, bold=False):
    """🚨 Bold is always eight bits, whatever its size. It is the home ring's
    label face, it is the text somebody has to read from a metre away, and there
    is exactly one of them — so the size rule above does not get to apply.
    (Reaching here with bold=True at 4 bpp is how the label ended up soft the
    first time this was changed: the size went up and the depth went down.)"""
    if bold:
        return BPP_SMALL
    return BPP_SMALL if size <= BPP_SPLIT else BPP_LARGE

# 🚨 What lv_font_montserrat_* actually carries above ASCII. This is the whole
#    of it: anything else a label draws has to come from a Chinese face, or it
#    is a blank box on the screen. tools/regress.sh checks the list against the
#    font LVGL ships rather than trusting it here.
MONTSERRAT_HAS = {0x00B0, 0x2022}      # degree sign, bullet


def sources():
    pats = ["main/*.c", "main/apps/*.c", "main/ble/*.c", "main/*.h", "main/apps/*.h"]
    found = []
    for pat in pats:
        found += glob.glob(os.path.join(REPO, pat))
    return sorted(set(found))


def literals(src):
    """Yields the string literals in a C file. Comments are skipped.

    🚨 A scan rather than a regex over the whole file, and the regex it replaces
       was wrong in two ways that both bake the wrong set silently: a quote
       inside a block comment opened a literal that ran on for pages, and a `//`
       inside a literal — "http://…" — looked like a comment worth stripping.
    """
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            i += 1
            out = []
            while i < n and src[i] != '"':
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i + 1]); i += 2; continue
                out.append(src[i]); i += 1
            i += 1
            yield "".join(out)
        elif c == "'":                      # a char literal, not text
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j + 1
        else:
            i += 1


def needed_chars():
    """Every character above ASCII that a label draws, anywhere under main/.

    🚨 **Above ASCII, not "Chinese".** The first version of this collected CJK
       ideographs only, and that quietly left out every Chinese punctuation mark:
       the fullwidth comma and question mark in wifi_setup, the parenthesis and
       comma in usb_screen, and the U+00B7 in the recorder's hint line. A
       missing glyph is a blank box whatever its Unicode block is, and the only
       place any of them show up is the screen — which is exactly where nobody
       looks until the badge is in their hand.
       tools/regress.sh now checks this property directly rather than by
       counting, so the next one of these is caught by the harness.

    🚨 Latin and digits stay out on purpose. They are meant to fall through to
       Montserrat through the font's fallback, which is what keeps the clock's
       digits and the F-keys in the shapes the rest of the interface uses.
    """
    chars = set()
    for path in sources():
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lit in literals(fh.read()):
                for ch in lit:
                    if ord(ch) >= 128 and ord(ch) not in MONTSERRAT_HAS:
                        chars.add(ch)
    return chars


def extract_faces(tmp):
    """Pulls the regular and the bold out of the collection as plain TTFs.

    🚨 lv_font_conv reads fonts through opentype.js, which does not handle a
       .ttc collection. Extracting first is not an optimisation, it is what
       makes the next step work at all.
    """
    try:
        from fontTools.ttLib import TTCollection
    except ImportError:
        sys.exit("  ✗ fontTools is missing — pip install fonttools")

    coll = TTCollection(SRC_TTC)
    out = {}
    for idx, tag in ((REGULAR_IDX, "regular"), (BOLD_IDX, "bold")):
        path = os.path.join(tmp, tag + ".ttf")
        coll.fonts[idx].save(path)
        out[tag] = path
    return out


def bake(ttf, size, symbols, name, bold=False):
    """One font. 🚨 --lv-fallback is what hooks it to Montserrat; see the note
    in the header about what that buys."""
    dest = os.path.join(OUT, name + ".c")
    cmd = ["npx", "--yes", "lv_font_conv",
           "--no-compress", "--bpp", str(bpp_for(size, bold)), "--format", "lvgl",
           "--font", ttf,
           "--symbols", symbols,
           "--size", str(size),
           "--lv-font-name", name,
           "--lv-fallback", "lv_font_montserrat_%d" % size,
           "-o", dest]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        sys.exit("  ✗ lv_font_conv failed for %s\n%s%s" % (name, r.stdout, r.stderr))
    return dest


# ── out-of-date detection ────────────────────────────────────
# 🚨 Size and character count are written into the first line of each generated
#    file, and compared against that rather than against timestamps. Touching an
#    unrelated file must not trigger a rebake, and adding one character to one
#    label must.
def stamp_of(size, nchars, bold, bpp):
    """🚨 bpp belongs in here. It changes the glyphs as much as the size does,
    and a stamp that left it out would let a change to it sit unnoticed —
    the files would look baked and quietly stay at the old depth."""
    return "/* mkfonts: size=%d chars=%d bold=%d bpp=%d */" % (
        size, nchars, 1 if bold else 0, bpp)


def is_stale(path, size, nchars, bold, bpp):
    try:
        with open(path, encoding="utf-8") as fh:
            first = fh.readline().rstrip("\n")
    except OSError:
        return True
    return first != stamp_of(size, nchars, bold, bpp)


def write_stamp(path, size, nchars, bold, bpp):
    with open(path, encoding="utf-8") as fh:
        body = fh.read()

    # 🚨 lv_font_conv emits its include as a choice:
    #
    #        #ifdef LV_LVGL_H_INCLUDE_SIMPLE
    #        #include "lvgl.h"
    #        #else
    #        #include "lvgl/lvgl.h"
    #        #endif
    #
    #    and the macro is not defined in this build, so the firmware took the
    #    else and stopped on a missing `lvgl/lvgl.h` — a header the ESP-IDF lvgl
    #    component does not put on the path that way. The simulator never saw it:
    #    it is handed a different include tree, so the same file compiles there.
    #    Collapsing the whole block to the simple form is not a workaround for a
    #    missing define — it is making these match every other generated file in
    #    the tree, tools/mkassets.py's icons included, which have always said
    #    `#include "lvgl.h"` and always built.
    body = body.replace(
        '#ifdef LV_LVGL_H_INCLUDE_SIMPLE\n#include "lvgl.h"\n'
        '#else\n#include "lvgl/lvgl.h"\n#endif\n',
        '#include "lvgl.h"\n')

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(stamp_of(size, nchars, bold, bpp) + "\n" + body)


def write_header():
    lines = ["/* Generated by tools/mkfonts.py. Do not edit by hand.",
             " *",
             " * 🚨 One font per size the interface draws Chinese at. Each falls back",
             " *    to the Montserrat of the same size for everything it does not",
             " *    carry — Latin, digits, punctuation and the LV_SYMBOL_* glyphs —",
             " *    so a single label may mix the two freely. */",
             "#pragma once",
             '#include "lvgl.h"',
             ""]
    for size in SIZES:
        lines.append("extern const lv_font_t font_zh_%d;" % size)
    for size in BOLD_SIZES:
        lines.append("extern const lv_font_t font_zh_%d_bold;" % size)
    lines.append("")
    with open(os.path.join(OUT, "fonts.h"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("  fonts.h written")


def main():
    force = "--force" in sys.argv

    chars = needed_chars()
    if not chars:
        sys.exit("  ✗ no Chinese found in the sources — nothing to bake")
    symbols = "".join(sorted(chars))

    if "--list" in sys.argv:
        print("  %d distinct characters" % len(chars))
        print("  sizes: " + ", ".join(str(s) for s in SIZES))
        print("  " + symbols)
        return 0

    if not os.path.exists(SRC_TTC):
        sys.exit("  ✗ the source font is not here: %s\n"
                 "    Point SRC_TTC at a CJK font that exists on this machine." % SRC_TTC)

    os.makedirs(OUT, exist_ok=True)

    todo = []
    for size in SIZES:
        todo.append(("font_zh_%d" % size, size, False))
    for size in BOLD_SIZES:
        todo.append(("font_zh_%d_bold" % size, size, True))

    # 🚨 --check exists so tools/regress.sh does not have to work out for itself
    #    what "out of date" means. It used to, by comparing the character count
    #    in each file's stamp against a count of its own — and when the rule for
    #    what counts as a needed character changed, the harness went on using the
    #    old rule and reported success on fonts that were missing glyphs. One
    #    implementation, one answer.
    if "--check" in sys.argv:
        behind = [name for name, size, bold in todo
                  if is_stale(os.path.join(OUT, name + ".c"), size, len(chars),
                              bold, bpp_for(size, bold))]
        if behind:
            print("  stale: " + ", ".join(behind))
            print("  the interface draws %d characters above ASCII" % len(chars))
            print("  run: python3 tools/mkfonts.py")
            return 1
        print("      %d characters above ASCII, %d fonts, all baked from the current sources"
              % (len(chars), len(todo)))
        return 0

    stale = [t for t in todo
             if force or is_stale(os.path.join(OUT, t[0] + ".c"), t[1],
                                  len(chars), t[2], bpp_for(t[1], t[2]))]
    if not stale:
        print("  all %d fonts are up to date (%d characters)" % (len(todo), len(chars)))
        write_header()
        return 0

    with tempfile.TemporaryDirectory(prefix="mkfonts-") as tmp:
        faces = extract_faces(tmp)
        for name, size, bold in stale:
            face = "bold" if bold else "regular"
            print("  baking %s — %d characters, %s face, %d bpp"
                  % (name, len(chars), face, bpp_for(size, bold)))
            path = bake(faces[face], size, symbols, name, bold)
            write_stamp(path, size, len(chars), bold, bpp_for(size, bold))

    write_header()
    subprocess.run([sys.executable, os.path.join(REPO, "tools", "sync_cmake.py")], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
