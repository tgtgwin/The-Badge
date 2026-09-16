#!/usr/bin/env bash
# Parses the sources the simulator leaves out.
#
# sim/build.py skips everything in ESP_ONLY — the board's half of port.h. With
# ESP-IDF installed, idf.py build is what checks them. Without it, this is the
# only thing standing between a typo in rec_store.c and a badge that will not
# boot, so it runs the compiler over them against tools/espstub.
#
# It is a spell-checker, not a run: it catches a misspelled field, a wrong
# argument count, a missing declaration. It cannot catch wrong behaviour.
#
#   tools/syntaxcheck.sh          the files that exist
set -eu
cd "$(dirname "$0")/.."

STUB="tools/espstub"
CC="${CC:-gcc}"

# 🚨 Grows as more of the board half comes under test. port_esp.c and display.c
#    are not here yet: they pull in esp_wifi, esp_netif, nvs, driver/gpio and
#    the whole BSP display API, and stubbing that is a bigger job than stubbing
#    the recording path was.
FILES="
main/adpcm.c
main/rec_store.c
main/rec_upload.c
main/usb_export.c
main/audio_dsp.c
"

echo "════ parsing the board-only sources (tools/espstub) ════"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

FAIL=0
CHECKED=0
for f in $FILES; do
    if [ ! -f "$f" ]; then
        echo "  · $f is not there yet — skipped"
        continue
    fi
    CHECKED=$((CHECKED + 1))
    if "$CC" -fsyntax-only -std=gnu11 -Wall -Wextra \
            -Wno-unused-parameter -Wno-unused-variable -Wno-unused-but-set-variable \
            -I "$STUB" -I main -I main/apps \
            "$f" > "$TMP" 2>&1; then
        echo "  ✓ $f"
    else
        echo "  ✗ $f"
        sed 's/^/      /' "$TMP"
        FAIL=1
    fi
done

if [ "$CHECKED" = 0 ]; then
    echo "  ✗ nothing was checked — the file list has gone stale"
    exit 1
fi

[ "$FAIL" = 0 ] && echo "  ✓ $CHECKED files parsed" || echo "  ✗ the board-only sources do not parse"
exit "$FAIL"
