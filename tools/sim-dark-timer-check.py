#!/usr/bin/env python3
"""Does anything still run once the display has gone dark?

🚨 The rule is that LVGL's timers stop with the display and the port timers do
   not (see port.h). That distinction is invisible in the source — the two kinds
   of callback look identical, and the only difference is which clock drives
   them. So it is measured instead of grepped for.

   The display is left alone until it times out by itself, and the battery
   journal has to keep writing afterwards. It is a port timer for exactly this
   reason: its whole purpose is the night with the screen off. On the old code
   it kept writing only by accident, because nothing had saved its handle to
   pause — which is why this check has something to fail on.

  python3 tools/sim-dark-timer-check.py
"""
import os, subprocess, sys, tempfile

D = os.path.dirname(os.path.abspath(__file__)) + "/.."
SIM = os.path.join(D, "sim", "badge_sim.exe")
if not os.path.exists(SIM):
    SIM = os.path.join(D, "sim", "badge_sim")
LOG = os.path.join(tempfile.gettempdir(), "sim-dark-timer.log")
ERR = open(LOG, "w")

p = subprocess.Popen([SIM, "--serve"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=ERR, cwd=D)


def send(c):
    p.stdin.write(c.encode()); p.stdin.flush()


def frame():
    send("R\n")
    h = p.stdout.readline().split()
    if not h:
        raise SystemExit("★ the simulator died")
    n = int(h[1])
    b = b""
    while len(b) < n:
        c = p.stdout.read(n - len(b))
        if not c:
            raise SystemExit("★ the simulator died")
        b += c


def step(ms, chunk=250):
    """🚨 In chunks, for two reasons.
    The simulator's 'P' command ignores anything past 5000 ms — a single
    200-second step is silently dropped, and the run then looks like "nothing
    ever fired". And port_timer_pump runs inside advance(), so one huge step
    would fire each timer once instead of the sixty times it really would.

    No frame() per chunk: the pump runs in advance(), not in the readback, so
    asking for pixels 900 times only makes the check slow.
    """
    for _ in range(max(1, ms // chunk)):
        send("P %d\n" % chunk)


step(800)
# 🚨 Not 'K 0' — that sets the auto-off timeout to 0, meaning NEVER, which is
#    what the other checks want and the opposite of what this one does. The
#    default is 30 s and is left alone.
send("P 300\n")
frame()
# The display times out by itself after 30 s. Well past that, and then long
# enough for several journal writes at one a minute.
step(50000)
frame()
step(200000)
frame()
send("Q\n")
try:
    p.wait(timeout=5)
except Exception:
    p.kill()
ERR.close()

lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()

# 🚨 "screen OFF" is launcher.c's own log line, not port_battery_mark(). That one
#    takes a bool that both of its call sites pass as true — the journal cares
#    where the line sits in the sequence, not what it says — so it can never be
#    used to tell the two states apart.
off_at = next((i for i, l in enumerate(lines) if "screen OFF" in l), None)
after = lines[off_at:] if off_at is not None else []
journal = [l for l in after if "[batt] idle-off" in l]

bad = 0
if off_at is None:
    print("  ★ the display never went dark by itself — the timeout is not running")
    print("    (note: the simulator's 'K 0' turns that timeout off entirely)")
    bad += 1
else:
    print("  the display went dark by itself at line %d of the log" % off_at)
if not journal:
    print("  ★ nothing wrote to the journal after the display went dark")
    print("    — the timer that has to outlive the display is being paused with it")
    bad += 1
else:
    print("  the journal kept writing in the dark (%d write%s, at 60 s apart)"
          % (len(journal), "" if len(journal) == 1 else "s"))

sys.exit(1 if bad else 0)
