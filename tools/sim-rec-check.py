#!/usr/bin/env python3
"""The recording screen: does the meter move, and does a pause really stop?

🚨 Both of these are the kind of thing that looks finished and is not. A meter
   is a bar whose width is set from a level — if the level never changes, or the
   bar is never resized, the screen still shows a bar and the file is still
   recorded, and the only way to find out is to hold the badge and watch. And a
   pause whose clock keeps running is worse than no pause: the file is shorter
   than the number on the screen said it would be.

   So neither is checked by reading the source. The meter is measured out of the
   pixels, and the clock is checked by comparing the pixels of the timer itself
   before and after several seconds of being paused — which is the only way to
   tell a stopped clock from a slow one.

  python3 tools/sim-rec-check.py
"""
import os
import subprocess
import sys
import tempfile

D = os.path.dirname(os.path.abspath(__file__)) + "/.."
SIM = os.path.join(D, "sim", "badge_sim.exe")
if not os.path.exists(SIM):
    SIM = os.path.join(D, "sim", "badge_sim")
ERR = open(os.path.join(tempfile.gettempdir(), "sim-rec-check.log"), "w")

# The meter sits inside the big button, 72 px below the centre of a 466 screen.
METER_Y = 233 + 72
METER_X0, METER_X1 = 233 - 84, 233 + 84
# The elapsed time, drawn at 48 px just above the middle of that button.
CLOCK = (233 - 90, 233 - 46, 233 + 90, 233 + 10)

p = subprocess.Popen([SIM, "--serve"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=ERR, cwd=D)


def send(c):
    p.stdin.write(c.encode()); p.stdin.flush()


def rd(n):
    b = b""
    while len(b) < n:
        c = p.stdout.read(n - len(b))
        if not c:
            raise SystemExit("★ the simulator died")
        b += c
    return b


def frame():
    send("F\n")
    h = p.stdout.readline().split()
    if not h:
        raise SystemExit("★ the simulator is not answering")
    return rd(int(h[1]))


def step(ms):
    send("P %d\n" % ms)


def press(x, y):
    send("T %d %d 1\n" % (x, y))


def release(x, y):
    send("T %d %d 0\n" % (x, y))


def px(buf, x, y):
    i = (y * 466 + x) * 3
    return buf[i], buf[i + 1], buf[i + 2]


def meter_width(buf):
    """How much of the bar is lit, in pixels, straight out of the framebuffer.

    🚨 Counting bright pixels rather than looking for one particular colour: the
       bar changes colour as the level climbs (green, amber, red), and a check
       that only knew about green would call a loud microphone a dead one."""
    n = 0
    for x in range(METER_X0, METER_X1):
        r, g, b = px(buf, x, METER_Y)
        if r + g + b > 200:
            n += 1
    return n


def clock_pixels(buf):
    x0, y0, x1, y1 = CLOCK
    return bytes(bytearray(buf[(y * 466 + x) * 3 + k]
                           for y in range(y0, y1, 2) for x in range(x0, x1, 2) for k in range(3)))


bad = 0


def check(what, ok, detail=""):
    global bad
    print("  %s %s%s" % ("✓" if ok else "✗", what, (" — " + detail) if detail else ""))
    if not ok:
        bad += 1


try:
    for _ in range(8):
        step(100); frame()
    # 🚨 Not 'K 0'. This check needs the screen to stay on for its whole run, and
    #    a screen that times out mid-measurement reads as a meter that stopped.
    send("K 60\n"); step(100); frame()
    send("A 4\n")                      # the recorder
    for _ in range(8):
        step(60); frame()

    idle = frame()
    check("the meter is hidden when nothing is being recorded",
          meter_width(idle) == 0, "%d px lit" % meter_width(idle))

    # ── start recording ──────────────────────────────────────
    press(233, 233); step(80); frame()
    release(233, 233); step(120); frame()
    step(400); frame()

    widths = []
    for _ in range(10):
        step(200); frame()
        widths.append(meter_width(frame()))
    check("the meter appears while recording", max(widths) > 0,
          "widest %d of %d px" % (max(widths), METER_X1 - METER_X0))
    # The simulator sweeps 0-100 over two seconds, so over two seconds of
    # sampling the width has to take more than a couple of values.
    check("the meter actually moves", len(set(widths)) >= 3,
          "saw %d distinct widths %s" % (len(set(widths)), sorted(set(widths))))

    # ── and it is drawn from the level, not at a fixed size ──
    check("the meter is not pinned at one size", max(widths) - min(widths) > 20,
          "%d px of travel" % (max(widths) - min(widths)))

    # ── the pause ────────────────────────────────────────────
    before = clock_pixels(frame())
    press(233, 233)
    for _ in range(24):                # a long press is 400 ms; hold 960
        step(40); frame()
    release(233, 233); step(80); frame()
    step(300); frame()
    paused = clock_pixels(frame())

    for _ in range(75):                # three seconds of being paused
        step(40); frame()
    after = clock_pixels(frame())
    check("the elapsed time stops while paused", after == paused,
          "the timer redrew" if after != paused else "frozen for 3 s")

    # ── and resumes ──────────────────────────────────────────
    press(233, 233)
    for _ in range(24):
        step(40); frame()
    release(233, 233); step(80); frame()
    step(1200); frame()
    touched = clock_pixels(frame())
    check("and starts again when the hold is repeated", touched != after)

finally:
    try:
        send("Q\n")
        p.wait(timeout=5)
    except Exception:
        p.kill()
    ERR.close()

sys.exit(1 if bad else 0)
