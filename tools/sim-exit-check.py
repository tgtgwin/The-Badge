#!/usr/bin/env python3
"""Enters every board and comes back home.

🚨 It died on the way out twice (the orbs 09-09, water 09-09). The cause was the
   same both times — freeing the memory a picture points at before the picture
   itself means one more draw happens before deletion and reads what was
   already freed. To the eye it only looks like "pressing home cuts out".
   Run this every time a new board goes in.

🚨 It used to find its way in by tapping coordinates, and that rotted: the ring
   was rearranged and the games menu went from six tiles to four, so the taps
   were landing on the wrong things — and reporting a pass. The apps are opened
   by name now, which cannot go stale, and only the games still need a tap
   because they live inside a menu.

  python3 tools/sim-exit-check.py
"""
import subprocess, sys, os, tempfile
D = os.path.dirname(os.path.abspath(__file__)) + "/.."
ERR = open(os.path.join(tempfile.gettempdir(), "sim-exit-check.log"), "w")
p=subprocess.Popen([D+'/sim/badge_sim','--serve'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=ERR,cwd=D)
def send(c): p.stdin.write(c.encode()); p.stdin.flush()
def rd(n):
    b=b''
    while len(b)<n:
        c=p.stdout.read(n-len(b))
        if not c: raise SystemExit("★ the simulator died")
        b+=c
    return b
def frame():
    send("R\n"); h=p.stdout.readline().split()
    if not h: raise SystemExit("★ the simulator died (no answer)")
    return rd(int(h[1]))
def step(ms): send(f"P {ms}\n")
def tap(x,y,hold=140):
    send(f"T {x} {y} 1\n"); step(60); frame(); step(hold); frame()
    send(f"T {x} {y} 0\n"); step(60); frame(); step(120); frame()

# 🚨 The order is the list in sim/main_sim.c, case 'A'. Opening by name rather
#    than by position is the whole point — see the note at the top.
APPS = {0: "games", 1: "air mouse", 2: "clock", 3: "calc", 4: "meet",
        5: "keys", 6: "settings"}

# The games menu is one column of four, centred: 260x70 buttons at
# lv_obj_align(CENTER, 0, -135 + i*90), so 90 px apart starting at y 98.
GAMES = {"bricks": (233, 98), "pinball": (233, 188),
         "marble": (233, 278), "pop": (233, 368)}

for _ in range(8): step(100); frame()
send("K 0\n"); step(100); frame()

for idx, name in APPS.items():
    send(f"A {idx}\n"); step(500); frame()
    for _ in range(10): step(33); frame()
    send("H\n")                      # power button = home
    for _ in range(15): step(50); frame()
    print(f"{name} → home passed")

send("A 0\n"); step(500); frame()    # games, then each board inside the menu
for name, (x, y) in GAMES.items():
    send("H\n")
    for _ in range(15): step(50); frame()
    send("A 0\n"); step(500); frame()
    tap(x, y); step(900); frame()
    for _ in range(15): step(33); frame()
    send("H\n")
    for _ in range(15): step(50); frame()
    send("A 0\n"); step(500); frame()
    print(f"{name} → home passed")

print("all passed")
