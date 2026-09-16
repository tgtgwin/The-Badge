#!/usr/bin/env python3
"""Receives recordings from the badge, one chunk at a time, and joins them up.

The badge records at 8 KB/s and holds about 52 minutes, so a meeting longer than
that has to be handed over as it goes. It does not stay on WiFi to do it: it
fills the buffer, joins the network, pushes what it has in a few seconds, and
leaves again. See docs/OPTIMIZATION-PLAN.md.

    python3 tools/serve/recv_server.py --root ./data --port 8790

Run it behind a TLS terminator (nginx, caddy) on a public host; see README.md in
this folder for a working nginx and systemd pair. The server itself speaks plain
HTTP and expects to be reachable only from the proxy.

🚨 The whole design rests on one idea: **the acknowledgement is the length of
the file on disk.** There is no offset column, no session state file, no
"last seen" bookmark — nothing that can disagree with the bytes. A client that
reconnects asks what is there and is told a number it can start from, and a
client that replays a chunk it already sent is recognised because the chunk
lands inside what is already written. Every other way of tracking progress needs
a second source of truth and a way to reconcile the two after a crash, and that
reconciliation is where data gets lost.

🚨 Nothing is acknowledged that is not on disk. The write is flushed and fsynced
before the reply goes out, because the badge deletes its copy the moment it
believes the server has it — an ack that is only in a page cache is a recording
destroyed by the next power cut.
"""
import argparse
import json
import os
import re
import struct
import sys
import tempfile
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ── the format, which the badge must agree with exactly ──────────
# 🚨 Straight out of main/adpcm.h. If these drift, the files still play and the
#    sound is wrong, which is the worst kind of wrong — so they are written as
#    the same names the firmware uses.
ADPCM_SAMPLE_RATE = 16000
ADPCM_BLOCK_BYTES = 256
ADPCM_BLOCK_SAMPLES = 505
ADPCM_WAV_HEADER_BYTES = 60

# 🚨 The badge sends 32 KB at a time: 128 whole ADPCM blocks, which is eight
#    4 KB flash sectors, so a chunk lines up with both the encoder's block
#    boundary and the flash erase granularity. A limit a little above that
#    rejects nonsense without being a second place the size is written down.
MAX_CHUNK = 256 * 1024

SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
META_FIELDS = ("device", "lang", "started")


def wav_header(data_bytes):
    """The 60-byte IMA-ADPCM header, matching adpcm_wav_header() in the firmware.

    🚨 60 and not 44 and not 48. A compressed format carries samples-per-block
    in the fmt chunk and adds a fact chunk, so both of the shorter numbers that
    look plausible are wrong. There used to be two of these in the firmware and
    they disagreed — the one in use was four bytes short and strict readers
    called every exported file corrupt.
    """
    blocks = data_bytes // ADPCM_BLOCK_BYTES
    samples = blocks * ADPCM_BLOCK_SAMPLES
    byte_rate = (ADPCM_SAMPLE_RATE // ADPCM_BLOCK_SAMPLES) * ADPCM_BLOCK_BYTES

    out = bytearray()
    out += b"RIFF"
    out += struct.pack("<I", 4 + 8 + 20 + 8 + 4 + 8 + data_bytes)   # == len - 8
    out += b"WAVEfmt "
    out += struct.pack("<I", 20)              # fmt chunk body, 20 for ADPCM
    out += struct.pack("<H", 0x0011)          # IMA ADPCM
    out += struct.pack("<H", 1)               # mono
    out += struct.pack("<I", ADPCM_SAMPLE_RATE)
    out += struct.pack("<I", byte_rate)
    out += struct.pack("<H", ADPCM_BLOCK_BYTES)
    out += struct.pack("<H", 4)               # bits per sample
    out += struct.pack("<H", 2)               # cbSize
    out += struct.pack("<H", ADPCM_BLOCK_SAMPLES)
    out += b"fact"
    out += struct.pack("<I", 4)
    out += struct.pack("<I", samples)
    out += b"data"
    out += struct.pack("<I", data_bytes)
    assert len(out) == ADPCM_WAV_HEADER_BYTES, len(out)
    return bytes(out)


def safe_name(s, fallback):
    """A filename component that cannot escape the directory or upset a shell."""
    s = re.sub(r"[^A-Za-z0-9_.-]", "", str(s))[:32]
    return s or fallback


class Store:
    """The files, and the lock that keeps two chunks from interleaving.

    🚨 One lock for the whole store rather than one per session. Uploads happen
    one at a time from one badge, the critical sections are a write to a local
    file, and a global lock cannot deadlock. A per-session lock would be more
    code for concurrency this is never going to see.
    """

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.incoming = os.path.join(self.root, "incoming")
        self.done = os.path.join(self.root, "recordings")
        os.makedirs(self.incoming, exist_ok=True)
        os.makedirs(self.done, exist_ok=True)
        self.lock = threading.Lock()

    def part_path(self, sid):
        return os.path.join(self.incoming, sid + ".adpcm.part")

    def meta_path(self, sid):
        return os.path.join(self.incoming, sid + ".json")

    def body_len(self, sid):
        """How much of this session is on disk. **This is the offset.**

        🚨 os.path.getsize on the .part file and nothing else. Not a counter
        kept beside it, not a length in the metadata — those are the second
        source of truth this design exists to avoid.
        """
        try:
            return os.path.getsize(self.part_path(sid))
        except OSError:
            return -1

    def read_meta(self, sid):
        try:
            with open(self.meta_path(sid), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def write_meta(self, sid, meta):
        # 🚨 Replaced, not updated: a half-written metadata file is worse than
        #    none, and nothing here is worth merging.
        tmp = self.meta_path(sid) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.meta_path(sid))

    def append(self, sid, data):
        """Appends at the end and makes it durable. Returns the new length.

        🚨 Appends rather than seeking, and the caller has already checked that
        the offset it was asked for equals the current length. An append cannot
        leave a hole; a seek-and-write can, and a hole in the middle of a
        recording is silent data loss that every length check still passes.
        """
        path = self.part_path(sid)
        with open(path, "ab") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        return os.path.getsize(path)

    def finalize(self, sid):
        """Writes the header in front and moves the file where people look."""
        meta = self.read_meta(sid)
        src = self.part_path(sid)
        size = os.path.getsize(src)
        if size == 0:
            raise ValueError("nothing was ever sent")

        started = meta.get("started") or int(time.time())
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
        name = "%s_%s_%s.wav" % (stamp,
                                 safe_name(meta.get("device", "badge"), "badge"),
                                 safe_name(meta.get("lang", "na"), "na"))
        dest = os.path.join(self.done, name)

        # 🚨 Written to a temporary file in the destination directory and then
        #    renamed. os.replace is atomic within a filesystem, so a reader
        #    never sees a half-written wav, and a crash leaves the .part file
        #    untouched to be finalised again.
        fd, tmp = tempfile.mkstemp(dir=self.done, suffix=".wav.part")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(wav_header(size))
                with open(src, "rb") as fh:
                    while True:
                        b = fh.read(1 << 20)
                        if not b:
                            break
                        out.write(b)
                out.flush()
                os.fsync(out.fileno())
            os.replace(tmp, dest)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        # The .part and the .json go together, and only once the wav exists.
        for p in (src, self.meta_path(sid)):
            try:
                os.unlink(p)
            except OSError:
                pass
        return name, size


class Handler(BaseHTTPRequestHandler):
    server_version = "badge-recv/1"
    store = None

    # ── plumbing ────────────────────────────────────────────────

    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code, why, **extra):
        extra["error"] = why
        self.reply(code, extra)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n < 0 or n > MAX_CHUNK:
            raise ValueError("chunk of %d bytes" % n)
        buf = b""
        while len(buf) < n:
            b = self.rfile.read(n - len(buf))
            if not b:
                break
            buf += b
        return buf

    def route(self):
        """Works out which endpoint this is. Returns (kind, sid, cmd).

        🚨 Three values because "no session id" means two different things: the
        create endpoint genuinely has none, and anything else without one is a
        bad URL. Collapsing those into a single None meant POST /session was
        answered with "no such endpoint" — the first thing the first client
        tries, and it fails immediately.
        """
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if len(parts) < 4 or parts[:3] != ["api", "v1", "rec"] or parts[3] != "session":
            return None, None, None
        if len(parts) == 4:
            return "create", None, None          # POST /api/v1/rec/session
        return "session", parts[4], (parts[5] if len(parts) > 5 else None)

    def query_offset(self):
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        for bit in q.split("&"):
            if bit.startswith("offset="):
                try:
                    return int(bit[7:])
                except ValueError:
                    return None
        return None

    # ── the four endpoints ──────────────────────────────────────

    def do_POST(self):
        kind, sid, cmd = self.route()
        if kind is None:
            return self.fail(404, "no such endpoint")

        if kind == "create":
            # A new session. The id is made here rather than by the badge: the
            # badge would have to remember it across a power cut, and this way
            # the only thing it has to keep is which recording it belongs to.
            try:
                meta = json.loads(self.body() or b"{}")
                if not isinstance(meta, dict):
                    raise ValueError("expected an object")
            except (ValueError, UnicodeDecodeError) as e:
                return self.fail(400, "bad metadata: %s" % e)
            new = uuid.uuid4().hex
            meta = {k: meta.get(k) for k in META_FIELDS if k in meta}
            meta["created"] = int(time.time())
            with self.store.lock:
                open(self.store.part_path(new), "wb").close()
                self.store.write_meta(new, meta)
            self.log_message("session %s opened (device=%s)", new[:8], meta.get("device"))
            return self.reply(200, {"session": new, "ack_offset": 0})

        if cmd == "finalize":
            if not SESSION_RE.match(sid):
                return self.fail(400, "bad session id")
            with self.store.lock:
                try:
                    name, size = self.store.finalize(sid)
                except FileNotFoundError:
                    return self.fail(404, "no such session")
                except ValueError as e:
                    return self.fail(409, str(e))
            self.log_message("session %s finalized as %s (%d bytes)", sid[:8], name, size)
            return self.reply(200, {"file": name, "bytes": size})

        return self.fail(404, "no such endpoint")

    def do_PUT(self):
        kind, sid, cmd = self.route()
        if kind != "session" or cmd is not None:
            return self.fail(404, "no such endpoint")
        if not SESSION_RE.match(sid):
            return self.fail(400, "bad session id")

        want = self.query_offset()
        if want is None:
            return self.fail(400, "offset is required")

        try:
            data = self.body()
        except ValueError as e:
            return self.fail(413, str(e))

        with self.store.lock:
            have = self.store.body_len(sid)
            if have < 0:
                return self.fail(404, "no such session")

            # 🚨 The three cases, and the middle one is the whole point of
            #    making the file length the offset.
            if want > have:
                # A hole. Writing here would silently lose everything between
                # `have` and `want`, and every length check afterwards would
                # still pass. Refuse and say where to resume from.
                return self.fail(409, "offset is past the end of what is stored",
                                 ack_offset=have)
            if want + len(data) <= have:
                # Already have it: the badge sent this before the connection
                # dropped, or before its own power cut, and never saw the ack.
                # Acknowledge again rather than write it twice.
                return self.reply(200, {"ack_offset": have, "replayed": True})
            if want < have:
                # Partly have it. Resume in the middle of the chunk.
                skip = have - want
                data = data[skip:]
                self.log_message("session %s: resuming %d bytes into a chunk", sid[:8], skip)

            # 🚨 CRC over what is actually stored, so it covers the part of a
            #    resumed chunk that is being written and not the replay of what
            #    was already there.
            crc = self.headers.get("X-Chunk-CRC32")
            if crc:
                got = "%08x" % (zlib.crc32(data) & 0xFFFFFFFF)
                if got.lower() != crc.strip().lower():
                    # 🚨 Nothing is written on a checksum failure. Appending and
                    #    then repairing is not a thing anyone can do — the file
                    #    would be corrupt in the middle and the length would say
                    #    it was fine.
                    self.log_message("session %s: CRC %s != %s, chunk dropped",
                                     sid[:8], got, crc)
                    return self.fail(422, "chunk checksum does not match",
                                     ack_offset=have, expected=got)

            new_len = self.store.append(sid, data)

        return self.reply(200, {"ack_offset": new_len})

    def do_GET(self):
        kind, sid, cmd = self.route()
        if kind != "session" or cmd is not None:
            return self.fail(404, "no such endpoint")
        if not SESSION_RE.match(sid):
            return self.fail(400, "bad session id")
        with self.store.lock:
            have = self.store.body_len(sid)
            if have < 0:
                return self.fail(404, "no such session")
        return self.reply(200, {"ack_offset": have, "state": "open"})


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="./data",
                    help="where incoming parts and finished recordings live")
    ap.add_argument("--host", default="127.0.0.1",
                    help="listen address; keep it on the loopback behind a proxy")
    ap.add_argument("--port", type=int, default=8790)
    args = ap.parse_args()

    Handler.store = Store(args.root)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("listening on %s:%d, root %s" % (args.host, args.port, Handler.store.root))
    print("  incoming   %s" % Handler.store.incoming)
    print("  recordings %s" % Handler.store.done)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
