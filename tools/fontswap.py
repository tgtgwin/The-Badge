#!/usr/bin/env python3
"""Points every text label at the Chinese font instead of bare Montserrat.

🚨 Why this is a blanket swap rather than a per-label decision. The obvious
   approach is to walk the ~75 font references and ask "is this one drawing
   Chinese?" — and it is the wrong one. The cost of getting a single one wrong
   is a label of blank boxes on a device, and it is invisible in review, because
   `&lv_font_montserrat_18` reads exactly like `&font_zh_18` at a glance.

   There is nothing to lose by swapping all of them either: each Chinese font
   falls back to the Montserrat of the same size for every glyph it does not
   carry, so a label of nothing but digits is drawn by exactly the same face as
   before. The line height comes from the primary font, which is the one real
   difference, and the simulator shows it up in a screenshot.

🚨 32 / 40 / 48 are not swapped. Nothing Chinese is drawn that large — those
   sizes are the recorder's REC and OK and a screenful of digits — and
   tools/mkfonts.py bakes no Chinese face for them. A Chinese word that strayed
   into one of those labels renders as boxes, and tools/regress.sh fails on it.

  python3 tools/fontswap.py --check
  python3 tools/fontswap.py
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The sizes mkfonts.py bakes a Chinese face for, in the order they appear.
SWAPPED = [14, 16, 18, 20, 24, 26]
KEPT = [32, 40, 48]

DECL = re.compile(r"&lv_font_montserrat_(\d+)")


def sources():
    for root, _, names in os.walk(os.path.join(REPO, "main")):
        for n in sorted(names):
            if n.endswith(".c"):
                yield os.path.join(root, n)


def add_include(text):
    """Puts the fonts header in among the includes, once.

    🚨 After the first quoted include rather than at the top: several of these
       files open with a long comment block explaining themselves, and pasting
       an include above that would push the explanation away from the code.
    """
    if '#include "fonts/fonts.h"' in text:
        return text, False
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith("#include \""):
            lines.insert(i + 1, '#include "fonts/fonts.h"')
            return "\n".join(lines), True
    return text, False


def main():
    check = "--check" in sys.argv
    changed, includes, per_size = [], 0, {s: 0 for s in SWAPPED}

    for path in sources():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()

        def swap(m):
            size = int(m.group(1))
            if size in SWAPPED:
                per_size[size] += 1
                return "&font_zh_%d" % size
            return m.group(0)

        new = DECL.sub(swap, text)
        if new == text:
            continue
        new, added = add_include(new)
        includes += 1 if added else 0
        changed.append(os.path.relpath(path, REPO))
        if not check:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)

    print("  %d files %s · %d font references · %d includes added"
          % (len(changed), "would change" if check else "changed",
             sum(per_size.values()), includes))
    for s in SWAPPED:
        if per_size[s]:
            print("      montserrat_%d → font_zh_%d  (%d places)" % (s, s, per_size[s]))
    missing = [s for s in SWAPPED if per_size[s] == 0]
    if missing:
        print("  ✗ no label uses these sizes any more: %s" % missing)
        print("      either the interface changed or this list is stale")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
