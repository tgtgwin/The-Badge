#!/usr/bin/env python3
"""Records something, uploads it, and plays it back — on this machine.

This is the whole chain, with no board in it:

    pcm ── firmware encoder ──> adpcm ── real protocol ──> server
                                                             │
    pcm <── firmware decoder ── adpcm <── wav ───────────────┘

Every piece is the shipping one. The encoder and decoder are `main/adpcm.c`,
linked unchanged into tools/adpcm_tool.c. The upload goes over the real HTTP
endpoints against the real recv_server.py.

What it catches that nothing else does:

  · the WAV header the server writes differing from the one the firmware writes
    — checked byte for byte, not field by field, because the field that was
    wrong once was the RIFF length and it is four bytes that nobody reads
  · the decoder not being the encoder's inverse. Its shift order is easy to get
    backwards, and backwards comes out as noise that still sounds like speech
  · the chunker splitting a block, which would decode to garbage from that point

    python3 tools/serve/test_roundtrip.py

Needs a C compiler. Uses ffprobe or afinfo if either is around, as a second
opinion on the file from outside this repository.
"""
import json
import math
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
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SERVER = os.path.join(HERE, "recv_server.py")
TOOL_C = os.path.join(ROOT, "tools", "adpcm_tool.c")

SAMPLE_RATE = 16000
BLOCK_BYTES = 256
BLOCK_SAMPLES = 505
CHUNK = 32768            # what the badge sends: 128 blocks, 8 flash sectors
# 🚨 Long enough to need several chunks. At one chunk the test would pass
#    against an uploader that cannot resume, cannot append, and cannot do
#    anything a second time — which is the entire interesting part of it.
SECONDS = 12.0

fails = []
checks = 0


def check(what, ok, detail=""):
    global checks
    checks += 1
    if ok:
        print("  ✓ %s%s" % (what, (" — " + detail) if detail else ""))
    else:
        print("  ✗ %s%s" % (what, (" — " + detail) if detail else ""))
        fails.append(what)


def build_tool(tmp):
    exe = os.path.join(tmp, "adpcm_tool")
    cmd = ["cc", "-O2", "-I", os.path.join(ROOT, "main"), "-o", exe,
           TOOL_C, os.path.join(ROOT, "main", "adpcm.c")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr)
        raise SystemExit("★ the codec would not compile")
    return exe


def speech_like(n):
    """Something with the shape of a voice: a few harmonics under an envelope.

    🚨 Not a pure tone. ADPCM is built around the statistics of speech, and a
    single sine is the one signal it handles almost perfectly — a decoder with
    the wrong shift order can still come out looking right on it.
    """
    out = bytearray()
    for i in range(n):
        t = i / SAMPLE_RATE
        env = 0.5 + 0.5 * math.sin(2 * math.pi * 1.7 * t)
        env *= 0.4 + 0.6 * math.sin(2 * math.pi * 0.7 * t) ** 2
        v = (0.62 * math.sin(2 * math.pi * 137 * t)
             + 0.30 * math.sin(2 * math.pi * 311 * t + 0.5)
             + 0.18 * math.sin(2 * math.pi * 743 * t + 1.1)
             + 0.10 * math.sin(2 * math.pi * 1607 * t + 2.0))
        s = int(max(-1.0, min(1.0, v * env)) * 24000)
        out += struct.pack("<h", s)
    return bytes(out)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


def call(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def snr(orig, got):
    """Signal to noise, dB. The one number that says 'the same sound'."""
    n = min(len(orig), len(got)) // 2
    sig = noise = 0.0
    peak = 0
    for i in range(n):
        a = struct.unpack_from("<h", orig, i * 2)[0]
        b = struct.unpack_from("<h", got, i * 2)[0]
        sig += a * a
        d = a - b
        noise += d * d
        peak = max(peak, abs(d))
    if noise == 0:
        return float("inf"), 0
    return 10 * math.log10(sig / noise), peak


def outside_opinion(path):
    """Asks something that is not this repository whether the file is a wav."""
    for cmd in (["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,"
                 "sample_rate,channels", "-of", "json", path],
                ["afinfo", path]):
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                txt = (r.stdout or "") + (r.stderr or "")
                return cmd[0], txt
    return None, ""


def main():
    tmp = tempfile.mkdtemp(prefix="badge-roundtrip-")
    data = os.path.join(tmp, "data")
    port = free_port()
    base = "http://127.0.0.1:%d" % port
    srv = subprocess.Popen([sys.executable, SERVER, "--root", data, "--port", str(port)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tool = build_tool(tmp)
        print("──── what the firmware's codec does with %.0f seconds of speech ────"
              % SECONDS)

        n_samples = int(SAMPLE_RATE * SECONDS)
        pcm = speech_like(n_samples)
        raw = os.path.join(tmp, "speech.s16le")
        adp = os.path.join(tmp, "speech.adpcm")
        back = os.path.join(tmp, "back.s16le")
        open(raw, "wb").write(pcm)

        r = subprocess.run([tool, "encode", raw, adp], capture_output=True, text=True)
        check("it encodes", r.returncode == 0, (r.stderr or "").strip())
        adpcm = open(adp, "rb").read()
        check("the size is what the bit rate says",
              len(adpcm) == math.ceil(n_samples / BLOCK_SAMPLES) * BLOCK_BYTES,
              "%d bytes for %d samples" % (len(adpcm), n_samples))
        check("it is a whole number of blocks", len(adpcm) % BLOCK_BYTES == 0)

        # ── the round trip, with no server involved yet ──────────
        subprocess.run([tool, "decode", adp, back, str(n_samples)],
                       capture_output=True, text=True)
        decoded = open(back, "rb").read()
        db, peak = snr(pcm, decoded)
        check("the decoder inverts the encoder", db > 18.0,
              "%.1f dB, worst sample off by %d of 32767" % (db, peak))

        # ── now over the wire, in the chunks the badge will use ──
        print("──── uploading it the way the badge will ────")
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

        st, r = call("POST", base + "/api/v1/rec/session",
                     json.dumps({"device": "roundtrip", "lang": "zh",
                                 "started": 1789000000}).encode())
        url = base + "/api/v1/rec/session/" + r["session"]

        sent = 0
        chunks = 0
        while sent < len(adpcm):
            piece = adpcm[sent:sent + CHUNK]
            st, r = call("PUT", "%s?offset=%d" % (url, sent), piece,
                         {"X-Chunk-CRC32": "%08x" % (zlib.crc32(piece) & 0xFFFFFFFF)})
            if st != 200:
                raise SystemExit("★ upload failed at %d: %s" % (sent, r))
            sent = r["ack_offset"]
            chunks += 1
        check("every chunk is accepted", sent == len(adpcm),
              "%d chunks of %d bytes" % (chunks, CHUNK))

        # 🚨 A chunk boundary landing mid-block would decode to garbage from
        #    there on, and nothing else in the protocol would notice — the length
        #    and the checksums are all still right.
        check("no chunk boundary falls inside a block",
              all((i * CHUNK) % BLOCK_BYTES == 0 for i in range(chunks)))

        st, r = call("POST", url + "/finalize")
        check("it finalizes", st == 200, r.get("file", ""))
        wav = open(os.path.join(data, "recordings", r["file"]), "rb").read()

        print("──── the header, against the firmware's own ────")
        fw_hex = subprocess.run([tool, "header", str(len(adpcm))],
                                capture_output=True, text=True).stdout.strip()
        fw_hdr = bytes.fromhex(fw_hex)
        check("the server writes the header the firmware writes",
              wav[:len(fw_hdr)] == fw_hdr,
              "firmware %d bytes, server %d bytes" % (len(fw_hdr), len(wav) - len(adpcm)))
        check("the header is 60 bytes",
              len(wav) - len(adpcm) == 60, "%d" % (len(wav) - len(adpcm)))
        check("RIFF length is the file length minus eight",
              struct.unpack("<I", wav[4:8])[0] == len(wav) - 8)

        print("──── and the sound that comes out ────")
        got = os.path.join(tmp, "got.adpcm")
        open(got, "wb").write(wav[60:])
        decomp = os.path.join(tmp, "decompressed.s16le")
        subprocess.run([tool, "decode", got, decomp, str(n_samples)],
                       capture_output=True, text=True)
        db2, peak2 = snr(pcm, open(decomp, "rb").read())
        check("what survived the upload is the same recording", db2 > 18.0,
              "%.1f dB, worst sample off by %d" % (db2, peak2))
        check("the round trip through the server loses nothing",
              abs(db - db2) < 0.01, "%.2f dB against %.2f dB" % (db, db2))

        who, txt = outside_opinion(os.path.join(data, "recordings", r["file"]))
        if who:
            ok = ("adpcm_ima_wav" in txt) or ("16000" in txt)
            bits = [l.strip() for l in txt.splitlines()
                    if "codec" in l or "sample_rate" in l or "Data format" in l]
            check("%s reads it as IMA ADPCM at 16 kHz" % who, ok, " · ".join(bits[:2]))
        else:
            print("  · no ffprobe or afinfo here — skipped the second opinion")

    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d checks, %d failed" % (checks, len(fails)))
    if fails:
        for f in fails:
            print("  ✗ " + f)
        return 1
    print("→ all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
