#!/usr/bin/env python3
"""Walks the upload protocol through the things that actually go wrong.

Every case here is one the badge will hit in a meeting room: a replayed chunk
after a dropped connection, a chunk lost to a CRC failure, a gap from a
half-written chunk, a power cut between the last chunk and finalize.

    python3 tools/serve/test_protocol.py

It starts the real server on a spare port with a temporary root, so it tests the
thing that ships rather than a copy of it.
"""
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "recv_server.py")

fails = []
checks = 0

# A fixed instant so the file name is predictable. 2026-09-10 12:26:40 UTC.
STARTED = 1789000000


def check(what, got, want):
    global checks
    checks += 1
    if got == want:
        print("  ✓ %s" % what)
    else:
        print("  ✗ %s\n      got  %r\n      want %r" % (what, got, want))
        fails.append(what)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def call(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def chunk(seed, n):
    """Deterministic bytes, so a CRC failure is reproducible."""
    out = bytearray()
    x = seed
    while len(out) < n:
        x = (x * 1103515245 + 12345) & 0xFFFFFFFF
        out += struct.pack("<I", x)
    return bytes(out[:n])


def main():
    root = tempfile.mkdtemp(prefix="badge-recv-test-")
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    p = subprocess.Popen([sys.executable, SERVER, "--root", root, "--port", str(port)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/api/v1/rec/", timeout=0.5)
                break
            except urllib.error.HTTPError:
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("★ the server never came up")

        print("──── opening a session ────")
        st, r = call("POST", base + "/api/v1/rec/session",
                     json.dumps({"device": "badge01", "lang": "zh",
                                 "started": STARTED}).encode())
        check("a session is opened", st, 200)
        sid = r.get("session", "")
        check("it starts empty", r.get("ack_offset"), 0)
        check("the id looks like a uuid4 hex", len(sid) == 32 and
              all(c in "0123456789abcdef" for c in sid), True)
        url = base + "/api/v1/rec/session/" + sid

        print("──── asking where to resume from ────")
        st, r = call("GET", url)
        check("a fresh session reports 0", (st, r.get("ack_offset")), (200, 0))

        print("──── sending a chunk ────")
        a = chunk(1, 4096)
        st, r = call("PUT", url + "?offset=0", a,
                     {"X-Chunk-CRC32": "%08x" % (zlib.crc32(a) & 0xFFFFFFFF)})
        check("it is accepted", (st, r.get("ack_offset")), (200, 4096))

        print("──── the same chunk again, as after a dropped connection ────")
        st, r = call("PUT", url + "?offset=0", a,
                     {"X-Chunk-CRC32": "%08x" % (zlib.crc32(a) & 0xFFFFFFFF)})
        check("a replay is acknowledged, not written twice",
              (st, r.get("ack_offset"), r.get("replayed")), (200, 4096, True))

        print("──── the next chunk ────")
        b = chunk(2, 4096)
        st, r = call("PUT", url + "?offset=4096", b,
                     {"X-Chunk-CRC32": "%08x" % (zlib.crc32(b) & 0xFFFFFFFF)})
        check("it is appended", (st, r.get("ack_offset")), (200, 8192))

        print("──── a chunk whose checksum does not match ────")
        c = chunk(3, 4096)
        st, r = call("PUT", url + "?offset=8192", c, {"X-Chunk-CRC32": "deadbeef"})
        check("it is refused", st, 422)
        st, r2 = call("GET", url)
        check("and nothing was written", r2.get("ack_offset"), 8192)

        print("──── a gap: the badge thinks it sent more than it did ────")
        st, r = call("PUT", url + "?offset=20000", c,
                     {"X-Chunk-CRC32": "%08x" % (zlib.crc32(c) & 0xFFFFFFFF)})
        check("it is refused rather than leaving a hole", st, 409)
        check("and it says where to resume from", r.get("ack_offset"), 8192)

        print("──── a chunk that half arrived before the cut ────")
        # The badge asks for 8192 and sends 4096, of which the first 2048 are
        # already stored. Only the tail is new, and the ack must land at
        # 8192 - 2048 + 4096 = 10240.
        half = chunk(4, 4096)
        st, r = call("PUT", url + "?offset=6144", half,
                     {"X-Chunk-CRC32": "%08x" % (zlib.crc32(half[2048:]) & 0xFFFFFFFF)})
        check("the overlap is skipped and the rest stored",
              (st, r.get("ack_offset")), (200, 10240))

        print("──── finalizing ────")
        st, r = call("POST", url + "/finalize")
        check("it finalizes", st, 200)
        name = r.get("file", "")
        # 🚨 Derived from the same `started` that was sent, not from today's
        #    date: the name is the recording's local time, and a hardcoded stamp
        #    here only ever passed on the day it was written.
        want_stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(STARTED))
        check("the file is named from the timestamp, device and language",
              name == "%s_badge01_zh.wav" % want_stamp, True)
        check("its length is the data plus the header", r.get("bytes"), 10240)

        print("──── the file that came out ────")
        path = os.path.join(root, "recordings", name)
        check("it is where the reply said", os.path.exists(path), True)
        with open(path, "rb") as fh:
            raw = fh.read()
        # 🚨 The field a short header gets wrong, and the one strict readers
        #    believe: RIFF size must be the file length minus 8.
        riff = struct.unpack("<I", raw[4:8])[0]
        check("the RIFF length is the file length minus eight",
              riff, len(raw) - 8)
        # 🚨 The layout, so the offsets below are checkable rather than magic:
        #    RIFF(4) size(4) WAVE(4) "fmt "(4) fmtsize(4) tag(2) chan(2)
        #    rate(4) byterate(4) blockalign(2) bits(2) cbSize(2) spb(2)
        #    "fact"(4) factsize(4) samples(4) "data"(4) datasize(4) = 60.
        check("the header starts with a RIFF/WAVE fmt chunk",
              raw[0:4] + raw[8:12] + raw[12:16], b"RIFF" + b"WAVE" + b"fmt ")
        check("it carries a fact chunk, as a compressed format should",
              raw[40:44], b"fact")
        check("the fmt body is the 20-byte ADPCM one",
              struct.unpack("<I", raw[16:20])[0], 20)
        check("the format tag is IMA ADPCM",
              struct.unpack("<H", raw[20:22])[0], 0x0011)
        check("the block alignment matches the firmware",
              struct.unpack("<H", raw[32:34])[0], 256)
        check("samples per block match the firmware",
              struct.unpack("<H", raw[38:40])[0], 505)
        check("the data chunk length is the payload",
              struct.unpack("<I", raw[56:60])[0], 10240)
        check("no payload was lost on the way",
              raw[60:], a + b + half[2048:])
        check("the part file is cleaned up",
              os.listdir(os.path.join(root, "incoming")), [])

        print("──── and now the same session again ────")
        st, r = call("GET", url)
        check("a finished session is gone, not resurrected", st, 404)

        print("──── opening one and losing power before any data ────")
        st, r = call("POST", base + "/api/v1/rec/session", b"{}")
        sid2 = r["session"]
        st, r = call("POST", base + "/api/v1/rec/session/%s/finalize" % sid2)
        check("finalizing an empty session is refused, not saved as a 60-byte file",
              st, 409)

    finally:
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
        shutil.rmtree(root, ignore_errors=True)

    print("\n%d checks, %d failed" % (checks, len(fails)))
    if fails:
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("→ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
