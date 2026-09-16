#!/usr/bin/env bash
# Checks in the sources whether anything we have been bitten by has come back.
#
# 🚨 Every check below is a mistake that actually happened, and most of them
#    happened twice. Remembering where a thing was fixed is not good enough —
#    the 'trace' of the fix is pinned down instead, so the same bug cannot come
#    back by being written in a different shape.
#
# 🚨 Two boards used to be checked here and are gone (the water and the planets,
#    and the orb photographs under them). Their checks went with them. What a
#    check may never become is a grep for a function name: one of the removed
#    ones had drifted into grepping for a file that no longer existed, and grep
#    returning "no such file" made it pass. See the note on the Saturn check for
#    what that looks like.
set -u
cd "$(dirname "$0")/.."
FAIL=0
ok()  { printf '  ✓ %s\n' "$1"; }
bad() { printf '  ✗ %s\n' "$1"; FAIL=1; }
has() { grep -qF "$2" "$1" 2>/dev/null; }

echo "════ drawing ════"
has main/display.c ".use_psram = false" \
  && ok "the draw buffer is in internal RAM (in PSRAM it grabs a scratch buffer per transfer and failed 13,943 times)" \
  || bad "the draw buffer is back in PSRAM"
# 🚨 It used to look for ".buffer_height = 16" literally. That value is tuned
#    against measured performance (raised to 24 on 09-09 with reasons given).
#    A check tied to a number called a perfectly good change wrong — a check
#    has to look at the meaning, not the name. The meaning to hold is "do not
#    hold the whole screen at once" (internal RAM blows up).
BH=$(grep -oE "\.buffer_height = [0-9]+" main/display.c | grep -oE "[0-9]+")
[ -n "$BH" ] && [ "$BH" -ge 8 ] && [ "$BH" -le 64 ] \
  && ok "only ${BH} rows go out at a time (the whole screen is not held at once)" \
  || bad "the band height looks wrong (currently '${BH:-none}')"
sed -n "/static esp_err_t panel_cmd/,/^}/p" main/display.c | grep -q port_lock \
  && ok "panel commands take the lock (changing brightness fought drawing over SPI)" \
  || bad "panel_cmd does not take the lock"

echo "════ radio ════"
has main/ble/hid_mouse.c "esp_hid_gap_deinit()" \
  && ok "bringing BLE down brings HID GAP down too (left up, it never comes back)" \
  || bad "esp_hid_gap_deinit is missing"
# 🚨 Not looking at what xTaskCreate returned leaves the state stuck on "in
#    progress" when it fails (which is when internal RAM is thin). The screen
#    never leaves "looking around..." or "connecting...", and the latch stops
#    that boot from even trying again (09-13).
#    Do not count them — tasks come and go. The meaning is "if it was created,
#    check that it was".
python3 - <<'EOF' && ok "the radio tasks check that they were created" || bad "a radio task ignores what xTaskCreate returned"
import re, sys
src = open("main/port_esp.c", encoding="utf-8").read()
miss = [m.group(1) for m in re.finditer(r'xTaskCreate\((\w+),\s*"(?:wifiscan|wifitry|timesync)"[^;]*;', src)
        if "pdPASS" not in m.group(0) and "pdPASS" not in src[max(0, m.start()-80):m.start()]]
if miss:
    print("  not checked:", ", ".join(miss)); sys.exit(1)
EOF

echo "════ sound ════"
has main/port_esp.c "tone_muted" \
  && ok "muted, it does not hold the codec (it used to push silence and burn power)" \
  || bad "it holds the codec while muted"
grep -q "s_tone_held ? 20000000" main/port_esp.c \
  && ok "even when held, it lets go after 20 quiet seconds" || bad "it holds the codec indefinitely"

echo "════ the journal ════"
has main/port_esp.c "JRN_BREAK" \
  && ok "unplugging after a charge does not wipe the journal (a boundary is marked and it carries on)" \
  || bad "the journal is wiped on unplug"
# The **behaviour** is checked, not the wording: does a rising level re-baseline?
grep -qE 'pct > s_last_pct' main/port_esp.c \
  && grep -qE 's_last_pct = pct;[^\n]*' main/port_esp.c \
  && ok "a level pushed down by load is not counted twice" || bad "double counting of the level is back"

echo "════ the way out (never free memory before the picture) ════"
# 🚨 The lesson stands even though the two boards that taught it are gone: a
#    canvas points into a buffer somebody else owns, and LVGL draws one more
#    frame while the closing animation runs. Freeing the buffer on the way out
#    means that frame reads freed memory.
#    Calc is the board that still holds a canvas, and it hands the buffer back
#    from a timer that fires after the animation rather than from leave().
grep -A6 "^static void free_cb" main/apps/app_calc.c | grep -q "port_big_free(s_cbuf)" \
  && ok "calc hands its canvas buffer back from the timer" \
  || bad "calc no longer frees the canvas buffer at all"
python3 - <<'EOF' && ok "calc does not free the canvas buffer while the screen is still up" || bad "calc frees the canvas buffer while an object still points at it"
import re, sys
src = open("main/apps/app_calc.c", encoding="utf-8").read()
m = re.search(r"static void leave\(void\)\n\{(.*?)\n\}", src, re.S)
if not m:
    print("  cannot find leave()"); sys.exit(1)
if "port_big_free" in m.group(1):
    print("  leave() frees the buffer straight away"); sys.exit(1)
if "lv_timer_create(free_cb" not in m.group(1):
    print("  leave() does not schedule the deferred free"); sys.exit(1)
EOF

echo "════ axes and directions (wrong several times on the hardware) ════"
grep -q "s_mvx -= gy" main/apps/app_games.c && grep -q "s_mvy -= gx" main/apps/app_games.c \
  && ok "marble: the IMU axes being 90 degrees off the screen is accounted for" || bad "the marble axes are back to front"
grep -q "float lat = gy;" main/apps/app_games.c \
  && ok "tilt board: it goes the way it is tilted" || bad "the tilt board's left and right are reversed"

echo "════ air mouse ════"
LAST=$(grep -n 'air_paint()' main/apps/app_mouse.c | tail -1 | cut -d: -f1)
BTN=$(grep -n 's_air_l = mk_click_btn' main/apps/app_mouse.c | cut -d: -f1)
[ -n "$LAST" ] && [ -n "$BTN" ] && [ "$LAST" -gt "$BTN" ] \
  && ok "it shows and hides after the buttons exist (called first, they stay hidden)" \
  || bad "air_paint is called before the buttons — the left and right buttons never appear"

echo "════ rebuilding home ════"
# 🚨 A check stood here asserting that turning the page rebuilt home —
#    launcher_show_home() only re-shows a screen that already exists, so the
#    page number changed and the screen did not. There are no pages now, so the
#    check went with the swipe. The two below are about home being rebuilt on
#    the way back from an app, which still happens every time.

grep -qE 's_prev_p = -2;' main/launcher.c \
  && ok "a rebuilt home always writes the battery number once (without it, LVGL's default 'Text' stays)" \
  || bad "the battery label on a new home can be left as the default text"
grep -B2 's_batt_timer = lv_timer_create' main/launcher.c | grep -q 'lv_timer_delete' \
  && ok "rebuilding home deletes the old battery timer (otherwise they pile up with every page turn)" \
  || bad "timers pile up when home is rebuilt"

echo "════ does it run anywhere ════"
# 🚨 -Wl,--start-group is GNU ld's. Apple's linker does not know it and the link dies
grep -q 'sys.platform == "darwin"' sim/build.py \
  && ok "the Mac linker is handled separately" || bad "the link dies on a Mac (--start-group)"
# 🚨 The name the badge appears under differs by machine — documenting only Linux leaves people lost elsewhere
grep -q "cu.usbmodem" tools/setup.sh \
  && ok "the port note matches the machine" || bad "the port note covers only Linux"
# 🚨 An absolute path written into the tool stops it running at all on another
#    machine. Do not check for one particular home directory — that only finds
#    the one machine it was written on, and puts somebody's username in a public
#    repository. Any absolute path outside the repo is the fault.
python3 - <<'EOF' && ok "no machine-specific path is written into the asset tool" || bad "an absolute path is written into mkassets.py"
import re, sys
src = open("tools/mkassets.py", encoding="utf-8").read()
bad = re.findall(r"""["'](/home/[^"']*|/Users/[^"']*|/mnt/[a-z]/[^"']*|[A-Za-z]:\\[^"']*)["']""", src)
if bad:
    print("  absolute path in the source:", " · ".join(sorted(set(bad)))); sys.exit(1)
EOF
# 🚨 Putting the simulator's LVGL in managed_components makes idf.py refuse it later
! grep -q "managed_components/lvgl__lvgl$" tools/setup.sh \
  && ok "the simulator's LVGL lives in sim/lvgl" || bad "the simulator's LVGL is dirtying managed_components"
# 🚨 It has to stand up on Windows too — Python drives the build, win_compat.h is the POSIX shim
[ -f sim/build.py ] && [ -f sim/win_compat.h ] \
  && ok "the simulator stands up on Windows too" || bad "Windows support is missing"
# 🚨 Multi-line logs are joined into one line first, so there are no false positives

echo "════ the handover documents ════"
# 🚨 These are the only authority the next person and the next LLM read. Without them nobody can follow.
[ -f README.md ] && ok "there is a README" || bad "README.md is missing"
[ -f NOTICE ]    && ok "there is a third-party notice" || bad "NOTICE is missing"
[ -f LICENSE ]   && ok "there is a licence" || bad "LICENSE is missing"
# Says so if the code has run well ahead of the documents (a nudge to fix it, not a failure)
if [ -f docs/HANDOFF.md ]; then
  NEW=$(git log --oneline -20 --format=%H -- main/ tools/ 2>/dev/null | head -1)
  DOC=$(git log --oneline -1 --format=%H -- docs/HANDOFF.md 2>/dev/null)
  AHEAD=$(git rev-list --count "$DOC..$NEW" -- main/ tools/ 2>/dev/null || echo 0)
  [ "${AHEAD:-0}" -gt 3 ] \
    && echo "  ⚠ the code is ${AHEAD} commits ahead of the handover documents — check whether something needs fixing"
fi

echo "════ is the bench off ════"
# 🚨 Flashed with it on, the badge opens apps by itself on every boot and takes the controls away
grep -qE "^add_compile_definitions\(BADGE_APPBENCH" CMakeLists.txt \
  && bad "the app bench is on" || ok "the app bench is off"

echo "════ are the check hooks at file scope ════"
# 🚨 A function nested inside another builds fine under GCC's nested-function
#    extension but cannot be called from outside. games_debug_play_bricks was
#    hidden that way once.
awk '/^static void brk_touch/,/^}/' main/apps/app_games.c | grep -q "games_debug" \
  && bad "a check function is nested inside brk_touch" || ok "the check functions are at file scope"

echo "════ waking the IMU ════"
# 🚨 The first readings after a sleep cannot be trusted — used as "level", the board sticks to one edge
grep -q "imu_settled" main/port_esp.c \
  && ok "right after waking it answers that it does not know yet" || bad "it hands over what it read the instant it woke"
# 🚨 A check stood here — "water substitutes a value while the IMU cannot be
#    trusted", reading `bool have = port_imu_accel3` and `if (!have)`. Its
#    subject is gone and it had no other board to move to: the remaining ones
#    test the call's return value directly rather than parking it in a local,
#    so a check rewritten to look for that would pass on shapes that mean
#    nothing. Rather than keep a check that only looks like coverage, the rule
#    is left to the two checks below and above, both of which read behaviour.

echo "════ starting BLE ════"
# 🚨 esp_bt_controller does not return an error when it cannot allocate — it
#    asserts inside itself (BLE assert emi.c 164) and the interrupt watchdog
#    reboots the board. Every error path in port_hid_start() is unreachable in
#    the one case that matters. Measured on hardware: BLE needs about 62 KB of
#    internal RAM, and the boot-time clock sync leaves 22 KB while it holds
#    WiFi — opening the Air Mouse in those ten seconds was a boot loop.
#    So the free-RAM check has to come BEFORE the call into the controller.
python3 - <<'EOF' && ok "BLE checks free internal RAM before touching the controller" || bad "BLE goes to the controller without checking internal RAM first"
import re, sys
src = open("main/ble/hid_mouse.c", encoding="utf-8").read()
m = re.search(r"bool port_hid_start\(void\)\s*\{(.*?)\n\}", src, re.S)
if not m: print("  cannot find port_hid_start()"); sys.exit(1)
body = m.group(1)
guard = body.find("heap_caps_get_free_size(MALLOC_CAP_INTERNAL)")
call  = body.find("esp_hid_gap_init(")
if guard < 0: print("  it never reads the free internal RAM"); sys.exit(1)
if call < 0:  print("  cannot find the call into the controller"); sys.exit(1)
if guard > call: print("  the check comes after the controller call, which is too late"); sys.exit(1)
if "return false" not in body[guard:call]: print("  it reads the RAM but does not refuse"); sys.exit(1)
EOF

echo "════ the mouse status text ════"
# 🚨 The label has to be written once when it is made — otherwise LVGL's default 'Text' stays
grep -A2 "s_state = lv_label_create" main/apps/app_mouse.c | grep -q "lv_label_set_text(s_state" \
  && ok "text is written when it is made" || bad "'Text' can be left behind"
# 🚨 Resetting the tracking value on entry is what makes the first update always write
grep -qE 's_prev_conn = -1;' main/apps/app_mouse.c \
  && ok "the tracking value is reset on entering the app" || bad "nothing is written when the state matches the last one"

echo "════ credentials ════"
# 🚨 Written into the code, flashing from a computer with no secrets.h loses the
#    badge its WiFi. The old example's "SSID here" overwrote perfectly good stored values.
# 🚨 Do not check by name — on 09-10 rec_upload.c moved from badge_creds_wifi to
#    badge_wifi_pick and a ✗ appeared against perfectly good code. Check the
#    meaning: (1) both places take it through a badge_ accessor, (2) nowhere
#    writes an SSID directly.
_creds_ok=1
for f in main/port_esp.c; do
  grep -qE "badge_(creds_wifi|wifi_pick)" "$f" || _creds_ok=0
done
# creds_seed in port_esp.c is where NVS is seeded, so it is the exception
grep -n "BADGE_WIFI_SSID" main/rec_upload.c main/apps/*.c >/dev/null 2>&1 && _creds_ok=0
[ "$_creds_ok" = 1 ] \
  && ok "credentials are read from the badge" || bad "credentials are written into the code"
! grep -qE '\.ssid = BADGE_WIFI_SSID' main/*.c \
  && ok "no written-in value goes into wifi_config" || bad "wifi_config is filled from a written-in value"
grep -qE '#define BADGE_WIFI_SSID +""' main/secrets.example.h \
  && ok "the example is empty (meaning: leave it alone)" || bad "the example holds a fake SSID"
# 🚨 Two examples and somebody copies the wrong one. The old secrets.h.example
#    carried the words "SSID here", and copying that and flashing overwrites the
#    perfectly good credentials stored in the badge's NVS (reproducing exactly
#    the accident fixed on 09-09).
[ ! -f main/secrets.h.example ] \
  && ok "there is only one credentials example" || bad "the old secrets.h.example is still there"

echo "════ the setup script's exit status ════"
# 🚨 `[ condition ] && echo` as the last line exits 1 when the condition is false.
#    A caller running set -e stops there.
tail -1 tools/setup.sh | grep -qx "exit 0" \
  && ok "the setup script exits 0" || bad "the setup script's exit status follows a condition"

echo "════ boards played by tilting ════"
# 🚨 The launcher counts only touch as activity. Games played by tilting had the screen go off mid-roll.
grep -q "lv_display_trigger_activity" main/apps/app_games.c \
  && ok "tilting counts as activity too" || bad "playing by tilt turns the screen off"
# 🚨 It must not go into boards driven by touch — touch already counts as activity.
#    Do not count occurrences (09-11: pinball adding one more call put a ✗
#    against perfectly good code). The meaning is "every function that reads the
#    tilt calls it, and every function that does not, does not".
python3 - <<'EOF' && ok "everywhere the tilt is read counts as activity" || bad "somewhere reads the tilt without counting activity"
import re, sys
src = open("main/apps/app_games.c", encoding="utf-8").read()
bad = []
for m in re.finditer(r"\n(?:static )?\w[\w \*]*?(\w+)\([^)]*\)\s*\n?\{", src):
    name = m.group(1)
    if name in ("tilt_is_input",): continue
    start = m.end()
    depth, i = 1, start
    while i < len(src) and depth:
        if src[i] == '{': depth += 1
        elif src[i] == '}': depth -= 1
        i += 1
    body = src[start:i]
    # The exception is written in the **code's own words**, not by name. A place
    # reached only from a tap has no reason to wake: touch already counted.
    if "only reached from a touch" in body: continue
    if "port_imu_accel(" in body and "tilt_is_input(" not in body:
        bad.append(name)
if bad:
    print("  reads the tilt without counting activity:", ", ".join(bad)); sys.exit(1)
EOF
# 🚨 The same rule for every board, not just the games file. Water was the one
#    that first showed it — steered by tilting the boat, nobody touched the
#    screen, and it went dark in 30 seconds (reported 09-09).
#    🚨 A board that holds the screen awake for its own reasons is exempt: the
#    air mouse reads tilt to drive the cursor, and .keep_awake = true means the
#    timeout never reaches it anyway. Counting tilt as activity there would be
#    noise, not safety.
python3 - <<'EOF' && ok "every board steered by tilt keeps the screen awake" || bad "a board steered by tilt lets the screen go dark"
import os, sys
bad = []
for name in sorted(os.listdir("main/apps")):
    if not name.endswith(".c"): continue
    src = open(os.path.join("main/apps", name), encoding="utf-8").read()
    if "port_imu_accel(" not in src: continue
    if ".keep_awake = true" in src: continue
    if "lv_display_trigger_activity" not in src and "tilt_is_input(" not in src:
        bad.append(name)
if bad:
    print("  steered by tilt but never wakes the screen:", ", ".join(bad)); sys.exit(1)
EOF

# ── removed with the boards they guarded ──────────────────────
# Four sections went with the water and the planets: clearing the water image,
# the frame yardstick, the orb textures, and the orbs' attitude. They were real
# checks while those files existed and there is nothing left for them to read.
#
# 🚨 The one worth remembering as a shape to avoid is the Saturn check:
#        ! grep -qi "saturn" main/apps/orb.c main/apps/orb.h
#    While those files existed it meant something. Once they were deleted grep
#    returned 2 — an error, not a non-match — `!` turned that into success, and
#    the check passed while asserting nothing at all. A check that reads a file
#    list rots silently the moment a file leaves. The replacements above read the
#    sources and count what they find instead, so a file that goes missing shows
#    up as a failure rather than a pass.

echo "════ recording space ════"
# 🚨 The space has to come back. The store used to keep a per-entry `sent` flag
#    and the only thing that ever set it was rec_mark_sent(), which nothing
#    called — so a badge that filled its 24 MB once could never record again.
#    The flag is gone. What decides a recording is finished with is
#    uploaded_abs, a single monotonic head, and dropping the entries below it is
#    what frees the space (09-08: all 12 exported, 3.9 minutes left).
grep -q "purge_uploaded" main/rec_store.c && grep -q "purge_uploaded();" main/rec_store.c \
  && ok "uploaded recordings come off the list" || bad "the space does not come back after uploading"
# 🚨 Shaking the list mid-recording has the microphone write into the wrong slot.
#    🚨 This check used to be written with a trailing backslash and no ok/bad, so
#    it ran and threw its answer away — it could never fail, and nobody noticed.
grep -A3 "static void purge_uploaded(void)$" main/rec_store.c | grep -q "if (s_active) return;" \
  && ok "the list is not shaken while the microphone writes into it" \
  || bad "an entry can be removed while the microphone holds its index"

echo "════ the ring ════"
# 🚨 These two are what make a ring of bytes safe to hand to an uploader.
#    Break the first and the uploader is told to send bytes that were never
#    written; break the second and a new recording writes over a meeting that
#    never reached the server.
grep -q "if (abs > s_dir.write_abs) abs = s_dir.write_abs;" main/rec_store.c \
  && ok "the uploaded head cannot pass the write head" \
  || bad "uploaded_abs may run past write_abs"
grep -q "e->start_abs + e->bytes > s_dir.uploaded_abs" main/rec_store.c \
  && ok "a recording refuses to overwrite audio that is not uploaded yet" \
  || bad "an un-uploaded recording can be written over"
# 🚨 The ring is a ring: DATA_START + data_size must land back on DATA_START, or
#    the wrap tears a file in half at the seam.
python3 - <<'EOF' && ok "the data area is a whole number of sectors, so the seam is clean" || bad "the ring does not wrap onto a sector boundary"
import re, sys
t = open("main/rec_store.c", encoding="utf-8").read()
if not re.search(r"#define DATA_START\s+\(SECTOR \* 2\)", t):
    print("  DATA_START is not two sectors"); sys.exit(1)
if "return s_part->size - DATA_START;" not in t:
    print("  the data area is not the partition minus the directory"); sys.exit(1)
EOF

echo "════ the recording directory ════"
# 🚨 One copy, erased and rewritten in place. A power cut in that window left no
#    directory at all — the checksum failed and every recording on the badge
#    became unreachable in one go. Two copies and a sequence number means the
#    other one is always intact, whatever happens to the one being written.
grep -q "dir_read_copy" main/rec_store.c && grep -q "s_dir.crc = crc32_of" main/rec_store.c \
  && grep -q "a.seq >= b.seq" main/rec_store.c \
  && ok "the directory keeps two checksummed copies" \
  || bad "the directory has one copy and can be lost whole"
# 🚨 The uploaded head moves every chunk. Writing it out every time would erase a
#    directory sector every few seconds and wear the directory out in weeks while
#    the data area lasts ten years.
grep -q "DIR_FLUSH_BYTES" main/rec_store.c \
  && grep -q "abs - s_dir_flushed_uploaded >= DIR_FLUSH_BYTES" main/rec_store.c \
  && ok "the uploaded head is not written out on every chunk" \
  || bad "the uploaded head is flushed per chunk and will wear the directory out"

echo "════ the WAV header ════"
# 🚨 There were two of these and they disagreed. The one in use said
#    `36 + data`, which is the length for a 44-byte PCM header; an ADPCM header
#    is 60 bytes, so it was four bytes short and anything that trusted the field
#    called the file corrupt. One implementation now, shared with tools/serve.
grep -q "ADPCM_WAV_HEADER_BYTES 60" main/adpcm.h \
  && ok "the header is the 60-byte one" || bad "the WAV header length is wrong again"
grep -q "adpcm_wav_header" main/usb_export.c \
  && ok "the export uses the shared header" || bad "usb_export went back to a header of its own"
! grep -qE "put32\(b \+ 4, 36 \+" main/usb_export.c \
  && ok "the four-bytes-short RIFF length is gone" || bad "the RIFF length is short again"
# 🚨 The file a strict reader rejects is the file a person cannot play.
python3 - <<'EOF' && ok "the RIFF length agrees with the header and the data" || bad "the RIFF length does not add up"
import re, sys
src = open("main/adpcm.c", encoding="utf-8").read()
m = re.search(r"size_t adpcm_wav_header.*?\n\}", src, re.S)
if not m:
    print("  adpcm_wav_header is gone"); sys.exit(1)
body = m.group(0)
# RIFF size = 4 + 8 + 20 + 8 + 4 + 8 + data = 52 + data, i.e. file length - 8,
# because the header is 60 bytes and 60 - 8 = 52.
if not re.search(r"put32\(out \+ 4,\s*4 \+ 8 \+ 20 \+ 8 \+ 4 \+ 8 \+ data_bytes\)", body):
    print("  RIFF size is not (header - 8) + data"); sys.exit(1)
if "return 60;" not in body:
    print("  the header does not say it is 60 bytes long"); sys.exit(1)
EOF

echo "════ the microphone conditioning ════"
# 🚨 Filter before compress. A DC offset eats the ADPCM step range, so the same
#    speech is encoded coarser for nothing; and the hardware gain has to leave
#    the AGC room to work, or the front end is already clipping.
grep -q "dsp_process(&s_dsp, pcm, CHUNK)" main/rec_store.c \
  && ok "the samples are filtered on the way in" || bad "the conditioning is not in the recording path"
python3 - <<'EOF' && ok "the conditioning happens before the encoder, not after" || bad "the order is wrong: compressing then filtering wastes the step range"
import sys
t = open("main/rec_store.c", encoding="utf-8").read()
d = t.find("dsp_process(&s_dsp, pcm, CHUNK)")
a = t.find("adpcm_encode_block(pcm, CHUNK, blk)")
if d < 0 or a < 0:
    print("  could not find both calls"); sys.exit(1)
if d > a:
    print("  adpcm_encode_block runs first"); sys.exit(1)
EOF
grep -q "define MIC_GAIN_DB             18.0f" main/audio_dsp.h \
  && ok "the hardware gain leaves the AGC headroom" || bad "the front end is pinned at the old 30 dB"

echo "════ flash headroom ════"
# 🚨 A file still being written must not be read. A build was left running in the
#    background while the checks ran alongside, and reading a size that was not
#    finished lost 258 KB for nothing (09-08).
if [ -f build/badge_fw.bin ] && [ ! -f build/.ninja_lock ]; then
  SZ=$(stat -c%s build/badge_fw.bin)
  LEFT=$(( 6*1024*1024 - SZ ))
  echo "  app $(( SZ/1024 ))KB · $(( LEFT/1024 ))KB left"
  # 🚨 What grows here is baked artwork and Chinese fonts, and both only ever
  #    grow. 512 KB is the margin that says the next one still fits; below it,
  #    the answer is to shrink a font subset or rework the partitions, not to
  #    shorten a label.
  [ "$LEFT" -gt 524288 ] && ok "at least 512KB free" \
    || bad "the app partition is tight — the fonts and icons have nowhere left to grow"
fi

echo "════ the sdkconfig defaults ════"
# 🚨 A misspelt symbol in sdkconfig.defaults is not an error. Kconfig prints one
#    line during reconfigure and carries on with the default, so the setting you
#    thought you made was never made. CONFIG_ESP_COREDUMP_CHECKSUM_SHA sat there
#    for weeks doing nothing (the symbol is _SHA256). Every line here has to turn
#    up in the generated sdkconfig.
if [ -f sdkconfig ]; then
python3 - <<'EOF' && ok "every symbol in sdkconfig.defaults reached sdkconfig" || bad "a symbol in sdkconfig.defaults does not exist"
import re, sys
have = set(re.findall(r"^#?\s*(CONFIG_[A-Z0-9_]+)", open("sdkconfig", encoding="utf-8").read(), re.M))
bad = []
for ln in open("sdkconfig.defaults", encoding="utf-8"):
    m = re.match(r"^(CONFIG_[A-Z0-9_]+)=", ln.strip())
    if m and m.group(1) not in have:
        bad.append(m.group(1))
if bad:
    print("  not a real symbol:", " · ".join(bad)); sys.exit(1)
EOF
else
  echo "  · no sdkconfig yet — skipped (try again after idf.py build)"
fi

echo "════ the build list ════"
# 🚨 sync_cmake.py rewrites main/CMakeLists.txt from a list of folders it holds
#    internally, so a folder missing from that list is dropped silently and the
#    link fails with `undefined reference` a long way from the cause. Every .c
#    under main/ has to appear in the build list.
python3 - <<'EOF' && ok "every source under main/ is in the build list" || bad "a source is missing from the build list"
import os, sys
lst = open("main/CMakeLists.txt", encoding="utf-8").read()
missing = [os.path.relpath(os.path.join(r, f), "main").replace(os.sep, "/")
           for r, _, fs in os.walk("main") for f in fs if f.endswith(".c")]
missing = [m for m in missing if m not in lst]
if missing:
    print("  not in main/CMakeLists.txt:", " · ".join(sorted(missing))); sys.exit(1)
EOF

echo "════ the fonts, and the characters nothing has ════"
# 🚨 Every character a label draws has to exist in the font that draws it, and a
#    missing one is a blank box — on a device, in front of a person, and nothing
#    else in the build so much as notices. mkfonts.py decides which characters
#    are needed and stamps the count into each file it writes, so this asks it
#    rather than reimplementing the rule: the harness doing its own version of
#    the rule is how the punctuation went missing for two rounds.
python3 tools/mkfonts.py --check \
  && ok "the baked fonts cover every character the interface draws above ASCII" \
  || bad "a label uses a character the fonts were not baked with (run tools/mkfonts.py)"

# 🚨 And the assumption underneath that check, verified rather than trusted.
#    mkfonts.py needs to know what Montserrat already covers, so it can leave
#    those characters out. If it is wrong in the direction of "Montserrat has
#    this" the character is left out of the Chinese face and falls back to a font
#    that does not have it — a blank box, from a wrong constant. So the set is
#    read out of the font LVGL ships and compared against the constant.
python3 - <<'EOF' && ok "exactly the characters Montserrat has are left to Montserrat" || bad "mkfonts.py is wrong about what Montserrat covers, which makes blank boxes"
import re, sys
src = open("sim/lvgl/src/font/lv_font_montserrat_16.c", encoding="utf-8").read()

cmaps = re.findall(r"\.range_start = (\d+), \.range_length = (\d+), "
                   r"\.glyph_id_start = \d+,\s*\n\s*\.unicode_list = (\w+), "
                   r".*?\.list_length = \d+", src, re.S)
lists = {m.group(1): [int(v, 16) for v in re.findall(r"0x[0-9a-fA-F]+", m.group(2))]
         for m in re.finditer(r"static const uint16_t (unicode_list_\d+)\[\] = \{(.*?)\};",
                              src, re.S)}

have = set()
for start, length, ulist in cmaps:
    start, length = int(start), int(length)
    if ulist == "NULL":
        have.update(range(start, start + length))
    else:
        # 🚨 The sparse list holds offsets from the range's own start, not
        #    codepoints. Reading them as codepoints gives a set of characters
        #    that are all in Unicode's private use area, which looks plausible
        #    and is completely wrong.
        have.update(start + v for v in lists.get(ulist, []))

# The private use area is the LV_SYMBOL_* icons, deliberately not our problem.
real = {c for c in have if not (0xE000 <= c <= 0xF8FF)}
claimed = set()
m = re.search(r"MONTSERRAT_HAS\s*=\s*\{([^}]*)\}", open("tools/mkfonts.py", encoding="utf-8").read())
if m:
    claimed = {int(v, 16) for v in re.findall(r"0x[0-9a-fA-F]+", m.group(1))}

# 🚨 Ignore the ASCII block: Latin is not baked into the Chinese faces by design,
#    and every printable ASCII character is in Montserrat anyway.
real = {c for c in real if c >= 128}
if real != claimed:
    print("  Montserrat actually has %s" % ", ".join("U+%04X" % c for c in sorted(real)))
    print("  mkfonts.py says it has %s" % ", ".join("U+%04X" % c for c in sorted(claimed)))
    print("  a character left out on a false assumption is a blank box on the screen")
    sys.exit(1)
print("      Montserrat adds %s above ASCII" % ", ".join("U+%04X" % c for c in sorted(real)))
EOF
# 🚨 And the other half of the same trap: 32, 40 and 48 have no Chinese face
#    baked, on purpose — what is drawn that large is digits, symbols and the
#    words REC and OK. Chinese that strays into one of those labels shows up as
#    boxes. The statement is taken whole (previous `;` to next) rather than by
#    line, because these calls are wrapped.
python3 - <<'EOF' && ok "no Chinese is drawn at a size that has no Chinese font" || bad "a label draws Chinese at 32/40/48, which have no Chinese face"
import glob, os, re, sys
CJK = re.compile(r"[\u4e00-\u9fff]")
bad = []
for path in sorted(glob.glob("main/**/*.c", recursive=True)):
    with open(path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    for m in re.finditer(r"&lv_font_montserrat_(32|40|48)\b", src):
        a = src.rfind(";", 0, m.start()) + 1
        b = src.find(";", m.end())
        if b < 0: b = len(src)
        if CJK.search(src[a:b]):
            line = src[:m.start()].count("\n") + 1
            bad.append("%s:%d" % (os.path.relpath(path), line))
if bad:
    print("  Chinese drawn at a Latin-only size: " + ", ".join(bad)); sys.exit(1)
EOF

echo "════ the recording receiver ════"
# 🚨 The protocol's whole safety argument rests on cases that only appear when
#    something has already gone wrong — a replayed chunk, a gap, a bad checksum,
#    a power cut before finalize. None of them is reachable by reading the code,
#    and all of them are silent when broken: the file is the right length and
#    the audio is wrong, or the badge deletes a recording the server never
#    finished. So the server is walked through each of them for real.
if [ -f tools/serve/test_protocol.py ]; then
  if python3 tools/serve/test_protocol.py >/tmp/badge-recv-test.log 2>&1; then
    ok "the upload protocol survives replays, gaps, bad checksums and a cut before finalize"
  else
    bad "the upload protocol mishandles one of the cases that matter"
    tail -20 /tmp/badge-recv-test.log | sed 's/^/      /'
  fi
fi
# 🚨 The WAV header exists in three places now — the firmware that records, the
#    firmware that exports over USB, and the server that joins the chunks. They
#    have already disagreed once (48 bytes against a correct 60, RIFF length four
#    short), and a disagreement here produces a file that plays and sounds wrong.
# 🚨 The header check below compares the *values* in three files. That is not
#    the same as the bytes coming out equal, and it cannot see the decoder at
#    all — a decoder whose shift order is backwards still returns plausible
#    numbers, and the noise it produces still sounds like speech. So the whole
#    chain is also walked for real: the firmware's own encoder, the real
#    protocol, the real server, the firmware's own decoder, and the signal
#    compared on the far side.
if command -v cc >/dev/null 2>&1 && [ -f tools/serve/test_roundtrip.py ]; then
  if python3 tools/serve/test_roundtrip.py >/tmp/badge-roundtrip.log 2>&1; then
    ok "a recording survives encode → upload → server → decode unchanged"
  else
    bad "a recording does not survive the trip through the server"
    tail -20 /tmp/badge-roundtrip.log | sed 's/^/      /'
  fi
fi
python3 - <<'EOF' && ok "the WAV header is the same 60 bytes everywhere" || bad "the WAV header has drifted between the firmware and the server"
import re, sys

h   = open("main/adpcm.h", encoding="utf-8").read()
srv = open("tools/serve/recv_server.py", encoding="utf-8").read()

def c_int(name):
    m = re.search(r"#define\s+%s\s+(\d+)" % name, h)
    return int(m.group(1)) if m else None

def py_int(name):
    m = re.search(r"^%s\s*=\s*(\d+)" % name, srv, re.M)
    return int(m.group(1)) if m else None

# 🚨 The three that decide whether the sound is right. A header length or a
#    block size that disagrees produces a file that opens, plays, and is wrong.
for name, want in (("ADPCM_WAV_HEADER_BYTES", 60),
                   ("ADPCM_BLOCK_BYTES", 256),
                   ("ADPCM_BLOCK_SAMPLES", 505),
                   ("ADPCM_SAMPLE_RATE", 16000)):
    a, b = c_int(name), py_int(name)
    if a is None or b is None:
        print("  %s: firmware has %r, server has %r" % (name, a, b)); sys.exit(1)
    if a != b or a != want:
        print("  %s: firmware %d, server %d, expected %d" % (name, a, b, want)); sys.exit(1)

# 🚨 And the RIFF length, which is the field that was actually wrong once: it is
#    the file length minus eight, so 4 + 8 + 20 + 8 + 4 + 8 + data = 52 + data.
if 'struct.pack("<I", 4 + 8 + 20 + 8 + 4 + 8 + data_bytes)' not in srv:
    print("  the server's RIFF length is not (header - 8) + data"); sys.exit(1)
if "put32(out + 4, 4 + 8 + 20 + 8 + 4 + 8 + data_bytes)" not in \
        open("main/adpcm.c", encoding="utf-8").read():
    print("  the firmware's RIFF length is not (header - 8) + data"); sys.exit(1)
EOF

echo "════ the board-only sources parse ════"
# 🚨 sim/build.py leaves out everything in ESP_ONLY, which is exactly the half
#    that talks to the hardware. With ESP-IDF installed idf.py build covers it;
#    without it, nothing did, and a typo in rec_store.c was found by flashing a
#    badge rather than by running a command. tools/syntaxcheck.sh parses them
#    against tools/espstub.
if [ -f tools/syntaxcheck.sh ]; then
  if bash tools/syntaxcheck.sh 2>&1 | grep -q "✗"; then
    bad "a board-only source does not parse"
    bash tools/syntaxcheck.sh 2>&1 | grep -A6 "✗" | sed 's/^/      /'
  else
    ok "the board-only sources parse"
  fi
fi

echo "════ timers (saving power once pinned the CPU) ════"
if grep -rn 'lv_timer_set_period(t' main/apps/*.c >/dev/null 2>&1; then
  bad "the period is changed inside a timer callback — lv_timer_handler goes round forever"
else
  ok "the period is not changed inside the callback (power is saved by skipping)"
fi
grep -q 'if (++skip' main/apps/app_mouse.c \
  && ok "it skips to save power when the screen is off" || bad "the screen-off power saving is missing"

echo "════ what keeps running with the display off ════"
# 🚨 The rule: an LVGL timer stops when the display does (idle_timers), so
#    anything that has to happen anyway is a port timer (port.h). Getting this
#    wrong is silent — the feature works all day on a desk and fails in a pocket.
python3 - <<'EOF' && ok "the alarm, the journal and the clock sync are outside LVGL's timers" || bad "something that has to run in the dark is on an LVGL timer"
import re, sys
src = open("main/launcher.c", encoding="utf-8").read()
bad = []
for fn in ("alarm_tick_cb", "batt_log_cb", "housekeep_cb", "ble_off_cb"):
    if re.search(r"lv_timer_create\(\s*%s" % fn, src):
        bad.append(fn + " is on an LVGL timer")
if not re.search(r'port_timer_start\("alarm"', src):
    bad.append("the alarm is not started as a port timer at all")
# 🚨 And the alarm must not be back inside idle_cb, which is paused along with
#    the display. That is exactly the shape the bug had.
m = re.search(r"static void idle_cb\(lv_timer_t \*t\)\n\{(.*?)\n\}\n", src, re.S)
if not m:
    bad.append("cannot find idle_cb")
elif "alarm_tick()" in m.group(1):
    bad.append("the alarm is checked from idle_cb again, which stops with the display")
if bad:
    print("  " + " · ".join(bad)); sys.exit(1)
EOF

echo "════ the display going dark ════"
# 🚨 Two invariants make the blanket pause safe, and both break by accident:
#      1. idle_timers has to walk every timer rather than a hand-kept list — a
#         list goes stale, and the ones it misses are the ones nobody meant to
#         leave running.
#      2. only launcher.c may pause a timer. The blanket resume assumes "was
#         already paused" means "the launcher paused it", so a second file
#         pausing its own would have it resumed behind that file's back.
python3 - <<'EOF' && ok "going dark pauses every LVGL timer, and only the launcher pauses any" || bad "the display-off timer rule has been broken"
import glob, os, re, sys
src = open("main/launcher.c", encoding="utf-8").read()
m = re.search(r"static void idle_timers\(bool screen_on\)\n\{(.*?)\n\}\n", src, re.S)
if not m:
    print("  cannot find idle_timers"); sys.exit(1)
if "lv_timer_get_next(NULL)" not in m.group(1):
    print("  idle_timers no longer walks the timer list"); sys.exit(1)
bad = []
for path in glob.glob("main/**/*.c", recursive=True):
    if os.path.basename(path) == "launcher.c": continue
    t = open(path, encoding="utf-8").read()
    if re.search(r"lv_timer_(pause|resume)\s*\(", t):
        bad.append(path)
if bad:
    print("  pauses its own timers, which the blanket resume would undo:", ", ".join(bad))
    sys.exit(1)
EOF
# 🚨 The escape hatch is opt-in in writing: an app declares that it needs its
#    timers in the dark, rather than launcher.c keeping a list of which do.
grep -q "timers_dark" main/app.h \
  && ok "an app can ask for its timers to survive the display going off" \
  || bad "there is no way to say a timer must keep going in the dark"
python3 - <<'EOF' && ok "only the clock claims the dark exemption" || bad "more apps claim it than were reasoned about"
import glob, sys
users = [p for p in sorted(glob.glob("main/apps/*.c"))
         if ".timers_dark = true" in open(p, encoding="utf-8").read()]
if users != ["main/apps/app_clock.c"]:
    print("  claims it:", ", ".join(users) or "nobody")
    print("  the countdown and the stopwatch are the whole reason this exists —")
    print("  recording runs in its own task and the air mouse should stop with the screen")
    sys.exit(1)
EOF

echo "════ breakout levels ════"
# 🚨 The meaning is checked, not the name. The three below were actually walked into on 09-10.
python3 - <<'EOF' && ok "the level table stays inside what a person can play" || bad "the level table is out of range"
import re, sys
src = open("main/apps/app_games.c", encoding="utf-8").read()
tab = re.search(r"BRK_LV\[BRK_LEVELS\] = \{(.*?)\};", src, re.S).group(1)
rows = re.findall(r"\{([^}]*)\}", tab)
if len(rows) != 5: print("  there are not 5 levels"); sys.exit(1)
prev_hits = -1
for i, r in enumerate(rows):
    f = [x.strip().rstrip('f') for x in r.split(',')]
    spd0, spdmax, padh, nrows = float(f[0]), float(f[1]), float(f[2]), int(f[3])
    # A speed no human wrist can follow is luck, not difficulty
    if spdmax > 9.0: print(f"  L{i+1} cap {spdmax} — past 9.0 it becomes luck"); sys.exit(1)
    if spd0 > spdmax: print(f"  L{i+1} starts above its cap"); sys.exit(1)
    if padh < 14.0:   print(f"  L{i+1} the paddle is too short"); sys.exit(1)
    if nrows not in (3, 4): print(f"  L{i+1} the row count is not 3 or 4"); sys.exit(1)
EOF
# 🚨 An obstacle made as an arc invalidates a large rectangle whole on every angle change
grep -q "s_obs\[i\] = dot(" main/apps/app_games.c \
  && ! grep -q "s_obs\[i\] = lv_arc_create" main/apps/app_games.c \
  && ok "the obstacle is a moving dot (no arc is redrawn)" \
  || bad "an obstacle made as an arc pushes a large area again every step"
# 🚨 The first row of bricks must not encroach on the back and tilt buttons.
#    Do not write the position in — on 09-11 the buttons moved inward to clear
#    the wall (-196 → -174) and the check put a ✗ against good code.
#    **It is worked out from the buttons' position.**
python3 - <<'EOF' && ok "the first row of bricks does not hide the buttons" || bad "the top row is hidden behind the buttons"
import re, sys
src = open("main/apps/app_games.c", encoding="utf-8").read()
def num(pat, cast=int):
    m = re.search(pat, src)
    return cast(m.group(1)) if m else None
dy  = num(r"#define GBTN_IN_DY\s+\(?(-?\d+)\)?")
h   = num(r"add_back_xy[\s\S]{0,400}?lv_obj_set_size\(b, \d+, (\d+)\)")
top = num(r"#define BRK_TOP\s+(\d+)")
if None in (dy, h, top):
    print(f"  cannot find the positions (dy={dy} h={h} top={top})"); sys.exit(1)
btm = 233 + dy + h // 2          # the bottom edge of the buttons (screen coordinates)
if top < btm:
    print(f"  the first brick row y={top} is above the buttons' bottom edge y={btm}"); sys.exit(1)
EOF

# 🚨 A newly made dot has to be put in place before it starts. Without that LVGL
#    leaves it at (0,0) and the ball sits in the corner while "tap to start" is up.
python3 - <<'EOF' && ok "a newly made dot is put in place before it starts" || bad "a dot is left at (0,0)"
import re, sys
src = open("main/apps/app_games.c", encoding="utf-8").read()
bad = []
for m in re.finditer(r"\n(?:static )?[\w \*]*?\b(\w+)\([^)]*\)\s*\n?\{", src):
    start = m.end(); depth, i = 1, start
    while i < len(src) and depth:
        if src[i] == '{': depth += 1
        elif src[i] == '}': depth -= 1
        i += 1
    body = src[start:i]
    for v in set(re.findall(r"(\w+)\s*=\s*dot\(", body)):
        if f"put({v}" in body: continue
        # a helper called by the same function may do the putting (followed one level deep)
        helped = False
        for call in set(re.findall(r"\b(\w+)\(\s*\)\s*;", body)):
            h = re.search(r"\n(?:static )?[\w \*]*?\b%s\([^)]*\)\s*\n?\{" % call, src)
            if not h: continue
            st = h.end(); d, j = 1, st
            while j < len(src) and d:
                if src[j] == '{': d += 1
                elif src[j] == '}': d -= 1
                j += 1
            if f"put({v}" in src[st:j]: helped = True; break
        if not helped:
            bad.append(f"{v} in {m.group(1)}()")
if bad:
    print("  made and never put:", " · ".join(bad)); sys.exit(1)
EOF

# 🚨 On a round screen the width at that height has to be measured before
#    choosing a y, and the text has to be checked for sitting behind something
#    else. On 09-11 the "Games" title was behind the first button, and it took a
#    simulator screenshot to notice.
python3 - <<'EOF' && ok "the menu title is neither hidden behind a button nor clipped" || bad "the menu title is hidden or clipped"
import re, sys, math
src = open("main/apps/app_games.c", encoding="utf-8").read()
def num(pat):
    m = re.search(pat, src); return int(m.group(1)) if m else None
title = num(r"lv_obj_align\(t, LV_ALIGN_CENTER, 0, (-?\d+)\)")
btn0  = num(r"lv_obj_align\(b, LV_ALIGN_CENTER, 0, (-?\d+) \+ i \* \d+\)")
bw    = num(r"lv_obj_set_size\(b, (\d+), \d+\)")
bh    = num(r"lv_obj_set_size\(b, \d+, (\d+)\)")
if None in (title, btn0, bw, bh): print("  cannot find the positions"); sys.exit(1)
if title + 14 > btn0 - bh // 2:
    print(f"  the title ({title}) is behind the first button's top edge ({btn0 - bh//2})"); sys.exit(1)
half = math.sqrt(233**2 - min(abs(title) + 14, 232)**2)
if half * 2 < 120:
    print(f"  the title row (dy {title}) is only {half*2:.0f}px wide and is clipped"); sys.exit(1)
# the buttons have to fit inside the circle at their own heights too
for i in range(4):
    dy = abs(btn0 + i * 90) + bh // 2
    if (bw / 2) ** 2 + dy ** 2 > 233 ** 2:
        print(f"  button {i+1}'s corner falls outside the circle"); sys.exit(1)
EOF

echo "════ pinball ════"
python3 - <<'EOF' && ok "the flipper pivots sit on the wall and the gap is wider than the ball" || bad "the pinball layout is wrong"
import re, sys, math
src = open("main/apps/app_games.c", encoding="utf-8").read()
def d(name):
    m = re.search(r"#define %s\s+([\d.]+)f?" % name, src)
    return float(m.group(1)) if m else None
wall, px, py, L, br = d("PB_WALL"), d("PB_FLIP_PX"), d("PB_FLIP_PY"), d("PB_FLIP_L"), d("PB_BR")
if None in (wall, px, py, L, br): print("  cannot find the constants"); sys.exit(1)
# 🚨 A pivot off the wall lets the ball run down outside the flipper and the game does not work
off = abs(math.hypot(px, py) - wall)
if off > 10: print(f"  the flipper pivot is {off:.0f}px off the wall — a path opens outside it"); sys.exit(1)
# 🚨 A middle gap narrower than the ball never drains, and too wide cannot be defended
rest = float(re.search(r"pb_flip_t\)\{ CX - PB_FLIP_PX, CY \+ PB_FLIP_PY,\s*([\d.]+)f", src).group(1))
gap = 2 * (px - L * math.cos(math.radians(rest)))
if gap <= br * 2: print(f"  the gap {gap:.0f}px is narrower than the ball {br*2:.0f}px — it will never drain"); sys.exit(1)
if gap > 120:     print(f"  the gap {gap:.0f}px — too wide to defend"); sys.exit(1)
EOF

echo "════ does a long press survive the screen being rebuilt ════"
python3 tools/sim-hold-check.py && ok "a long press still lands during a rebuild" || bad "the long press dies (deleting the pressed object makes LVGL ignore input until release)"

echo "════ does the WiFi screen get stuck when no answer comes ════"
python3 tools/sim-wifi-stall-check.py && ok "the scan screen finds its way out" || bad "the scan screen is stuck (asking again has no bound)"

echo "════ does anything still run in the dark ════"
# 🚨 Source-level checks cannot see this. The two kinds of timer callback look
#    identical; what differs is which clock drives them, and the only honest way
#    to check it is to let the display time out by itself and see whether the
#    journal is still writing afterwards.
python3 tools/sim-dark-timer-check.py >/dev/null 2>&1 \
  && ok "the timers that must outlive the display still fire with it dark" \
  || bad "the display going dark stops something that has to keep running (see sim-dark-timer-check.py)"

echo "════ the recording screen ════"
# 🚨 A meter and a pause are both things that look finished while doing nothing.
#    A bar whose width is set from a level that never changes still looks like a
#    bar, and a pause whose clock keeps running still looks like a pause — the
#    file is just shorter than the screen said. So the meter is measured out of
#    the framebuffer and the clock is compared against itself several seconds
#    later, which is the only way to tell stopped from slow.
if python3 tools/sim-rec-check.py >/tmp/badge-rec-check.log 2>&1; then
  ok "the meter tracks the microphone and a pause really stops the clock"
else
  bad "the recording screen's meter or pause does not do what it says"
  sed 's/^/      /' /tmp/badge-rec-check.log
fi

echo "════ the icons' edges ════"
# 🚨 Both halves of this were arrived at by measuring, and both are one line of
#    code that reads like a detail and is not:
#      · One reduction, not two. The builders used to shrink to ICON and leave
#        emit_icon to shrink that to ICON_OUT — a clean large reduction followed
#        by a small one, and the small one is where the aliasing lives.
#      · BOX for a large reduction, LANCZOS for a small one. LANCZOS has negative
#        lobes and rings; on the clock's thin rim that produced a beaded ring
#        instead of a continuous one, which is what "jagged" turned out to mean.
#        BOX is a plain area average with no ringing — but at a small reduction
#        it has almost no support and behaves like a nearest neighbour, so it is
#        not the answer everywhere either.
#    The failure is a picture rather than a number, so this checks the shape of
#    the code; the picture itself is judged from the screenshots.
python3 - <<'EOF' && ok "the icons are reduced once, with a filter that suits the ratio" || bad "the icon downscale will ring or alias — see emit_icon in tools/mkassets.py"
import re, sys
src = open("tools/mkassets.py", encoding="utf-8").read()
bad = []
hits = list(re.finditer(r"resize\(\(ICON_OUT", src))
if not hits:
    bad.append("nothing resizes to ICON_OUT")
elif len(hits) > 1:
    bad.append("%d places resize to ICON_OUT" % len(hits))
if re.search(r"resize\(\(ICON,\s*ICON\)", src):
    bad.append("something still shrinks to ICON first, which puts a second reduction in the way")
m = re.search(r"img\s*=\s*img\.resize\(\(ICON_OUT, ICON_OUT\),\s*(.*?)\)\n", src, re.S)
if not m:
    bad.append("cannot find the resize to ICON_OUT")
elif "Image.BOX if" not in m.group(1) or "LANCZOS" not in m.group(1):
    bad.append("the filter is not chosen from the reduction ratio")
if bad:
    print("  " + " · ".join(bad)); sys.exit(1)
print("      one reduction: BOX at 2x or more, LANCZOS below")
EOF

echo "════ the home ring ════"
# 🚨 Home was two pages and the sideways swipe turned them. Both are gone, and
#    so is sim-swipe-check.py, which had nothing left to swipe. What replaced it
#    is the arithmetic the ring rests on — and it is worth having, because every
#    term in it was changed by hand at some point and the failure mode is a
#    label running off the edge of a round screen, which no compiler notices.
#
#    Two things are asserted: that ICON_D matches the size the icons were baked
#    at (or LVGL rescales every one of them and the grain comes back), and that
#    every icon and label lands inside the circle. Four Chinese characters is the
#    worst case a label can be.
python3 - <<'EOF' && ok "the ring fits the circle, and the icons were baked at this size" || bad "the home ring does not fit, or ICON_D and the baked size disagree"
import math, re, sys
src = open("main/launcher.c", encoding="utf-8").read()
mk  = open("tools/mkassets.py", encoding="utf-8").read()

def num(text, name):
    # A C constant is `#define NAME 123`; the baked size is a Python assignment.
    for pat in (r"^#define\s+%s\s+(\d+)" % name, r"^%s\s*=\s*(\d+)" % name):
        m = re.search(pat, text, re.M)
        if m: return int(m.group(1))
    return None

ring, icon, scr = num(src, "RING_R"), num(src, "ICON_D"), num(src, "SCREEN_D")
out = num(mk, "ICON_OUT")
if None in (ring, icon, scr, out):
    print("  cannot read RING_R / ICON_D / SCREEN_D / ICON_OUT"); sys.exit(1)
if icon != out:
    print("  ICON_D is %d but mkassets emits %d" % (icon, out)); sys.exit(1)

apps = re.search(r"static const badge_app_t \*const s_apps\[\] = \{(.*?)\};", src, re.S)
if not apps: print("  cannot find the ring"); sys.exit(1)
n = apps.group(1).count("&app_")
if n < 2: print("  only %d icons on the ring" % n); sys.exit(1)

# 🚨 The label's size and offset are read out of launcher.c rather than written
#    here as well. They used to be, and the numbers in the two places came apart
#    the moment the label was made bigger — which is the failure this check
#    exists to catch, happening to the check itself.
m = re.search(r"int ny = y \+ ICON_D / 2 \+ (\d+);", src)
if not m: print("  cannot find the label offset"); sys.exit(1)
loff = int(m.group(1))
m = re.search(r"lv_obj_set_style_text_font\(nm, &font_zh_(\d+)_bold", src)
if not m: print("  cannot find the label font"); sys.exit(1)
lsize = int(m.group(1))
# and the bold face has to actually be baked at that size
mksrc = open("tools/mkfonts.py", encoding="utf-8").read()
m = re.search(r"BOLD_SIZES\s*=\s*\[([^\]]*)\]", mksrc)
bold_sizes = [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []
if lsize not in bold_sizes:
    print("  the label is %d px but mkfonts bakes bold at %s" % (lsize, bold_sizes or "nothing"))
    sys.exit(1)

# Four characters is the worst case a label can be (空中鼠标).
half_w = 4 * lsize / 2.0
half_h = lsize / 2.0
r, bad = scr / 2.0, []
pos = []
for i in range(n):
    ang = -math.pi / 2 + i * (2 * math.pi / n)
    pos.append((math.cos(ang) * ring, math.sin(ang) * ring))
labs = [(x, y + icon / 2 + loff) for x, y in pos]

# 🚨 Four constraints, and the third and fourth are the ones this check was
#    missing when the labels were first enlarged. It passed a layout whose
#    labels sat on top of the neighbouring icons — because a label is placed
#    radially outside its own icon, it reaches sideways into the angular sector
#    of the icons either side of it, and nothing that only compares each element
#    against the circle can see that.
for i in range(n):
    x, y = pos[i]
    if math.hypot(abs(x) + icon / 2, abs(y) + icon / 2) > r:
        bad.append("icon %d is off the edge" % i)
for i, (x, y) in enumerate(labs):
    if math.hypot(abs(x) + half_w, abs(y) + half_h) > r:
        bad.append("label %d runs off the edge" % i)
for i in range(n):
    for j in range(i + 1, n):
        if math.hypot(pos[i][0] - pos[j][0], pos[i][1] - pos[j][1]) < icon + 8:
            bad.append("icons %d and %d are touching" % (i, j))
        if (abs(labs[i][0] - labs[j][0]) < half_w * 2 + 6 and
                abs(labs[i][1] - labs[j][1]) < half_h * 2 + 6):
            bad.append("labels %d and %d overlap" % (i, j))
for i in range(n):
    for j in range(n):
        if i == j: continue
        dx = max(abs(labs[i][0] - pos[j][0]) - half_w, 0)
        dy = max(abs(labs[i][1] - pos[j][1]) - half_h, 0)
        if math.hypot(dx, dy) < icon / 2 + 4:
            bad.append("label %d sits on icon %d" % (i, j))
if bad:
    print("  " + " · ".join(sorted(set(bad)))); sys.exit(1)
print("  %d icons · ICON_D %d · RING_R %d · labels %d px" % (n, icon, ring, lsize))
EOF

echo "════ do large arrays hold internal RAM permanently ════"
if [ ! -f build/badge_fw.map ]; then
  echo "  · not built yet — skipped (try again after idf.py build)"
else
BSS=$(python3 tools/bss-size.py)
echo "  our code's .bss total ${BSS} bytes"
[ "$BSS" -lt 12000 ] && ok "under 12KB of permanent internal RAM" || bad "too much permanent internal RAM (it has to move to PSRAM)"
fi

echo
[ $FAIL -eq 0 ] && echo "→ all passed" || { echo "→ something has come back"; exit 1; }
