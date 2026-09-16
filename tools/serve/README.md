# The recording receiver

The badge holds about 52 minutes at 8 KB/s, so a meeting longer than that is
handed over as it goes: it fills its buffer, joins the network, pushes what it
has in a few seconds and leaves again. This is the other end of that.

```
tools/serve/
    recv_server.py         the server — Python 3, standard library only
    test_protocol.py       walks the protocol through the cases that go wrong
    nginx.conf.example     TLS termination and the proxy that goes with it
    badge-recv.service     systemd unit
```

Run it:

```sh
python3 tools/serve/recv_server.py --root ./data --port 8790
python3 tools/serve/test_protocol.py          # 28 checks, no network needed
```

## Where recordings end up

```
data/incoming/     <id>.adpcm.part   what has arrived so far
                   <id>.json         device, language, start time
data/recordings/   <stamp>_<device>_<lang>.wav   finished, playable
```

A `.part` file is the raw ADPCM stream with no header — the header cannot be
written until the length is known. `finalize` puts the 60-byte header in front
and moves it, and both files disappear from `incoming` in the same step.

## The one idea the protocol rests on

**The acknowledgement is the length of the `.part` file on disk.** There is no
offset column, no session state file, no last-seen bookmark — nothing that can
disagree with the bytes. Everything else follows:

| what happens | what the server does |
| --- | --- |
| the badge asks where to resume from | `GET` returns the file length |
| a chunk arrives at exactly that offset | append, fsync, return the new length |
| the same chunk arrives again (dropped reply, or the badge lost power before it saw the ack) | it lands inside what is stored, so it is acknowledged again and **not written twice** |
| a chunk arrives **past** the end | 409 with the length to resume from — writing there would leave a hole, and a hole in a recording is silent data loss that every length check still passes |
| a chunk **overlaps** the end | the part already stored is skipped and the rest appended |
| the checksum does not match | 422 and **nothing is written** — appending and repairing is not a thing anyone can do, the file would be corrupt in the middle and the length would say it was fine |

The alternative — tracking progress in a counter or a database beside the file —
needs a second source of truth and a reconciliation step after every crash, and
that reconciliation is where recordings get lost.

Two consequences worth knowing:

- **Nothing is acknowledged that is not on disk.** Every append is flushed and
  fsynced before the reply goes out, because the badge deletes its own copy the
  moment it believes the server has it.
- **A power cut is survivable at any point.** An unfinished session is a `.part`
  file and a `.json`; the next upload of that recording resumes from its length.
  Nothing needs cleaning up by hand.

## Endpoints

```
POST /api/v1/rec/session                     {"device","lang","started"}
     → 200 {"session":"<32 hex>","ack_offset":0}

GET  /api/v1/rec/session/{id}
     → 200 {"ack_offset":N,"state":"open"}

PUT  /api/v1/rec/session/{id}?offset=N       header X-Chunk-CRC32: <8 hex>
     body: raw ADPCM bytes
     → 200 {"ack_offset":N+len}     possibly {"replayed":true}
     → 409 {"ack_offset":<resume from here>}   gap
     → 422 {"ack_offset":…,"expected":…}       checksum

POST /api/v1/rec/session/{id}/finalize
     → 200 {"file":"…wav","bytes":N}
```

Chunks are 32 KB — 128 whole ADPCM blocks, which is eight 4 KB flash sectors, so
a chunk lines up with both the encoder's block boundary and the erase
granularity of the flash it came off.

🚨 The CRC is taken over **what is being written on this request**, not over the
bytes the badge sent. On a resumed chunk those differ, and checksumming the
whole thing would fail every resume.

## Putting it on the internet

The server speaks plain HTTP on purpose and should be reachable only from the
proxy. TLS belongs at the edge, where the certificate already lives and can be
renewed without touching the badge.

`nginx.conf.example` is a working server block. Short version:

```nginx
location /api/v1/rec/ {
    proxy_pass              http://127.0.0.1:8790;
    proxy_request_buffering off;      # a chunk should not wait for an EOF
    client_max_body_size    256k;     # a little above the badge's 32 KB
    proxy_read_timeout      120s;
}
```

🚨 `proxy_request_buffering off` matters more than it looks. With buffering on,
nginx holds the whole body before forwarding it, which turns a 32 KB chunk into
a round trip of its own and makes the upload take several times as long on a
slow mobile link. The badge is on a phone hotspot.

The badge uses `esp_crt_bundle`, so a publicly trusted certificate needs nothing
installed on it. A self-signed one would have to be baked into the firmware and
re-baked when it expires — worth avoiding if you can.

```sh
sudo cp badge-recv.service /etc/systemd/system/
sudo systemctl enable --now badge-recv
curl -sS -X POST https://your.host/api/v1/rec/session -d '{"device":"test"}'
```

## What this does not do

- **No accounts, no authentication.** Anyone who can reach the endpoint can
  upload. That was a deliberate scope decision — one badge, one owner, and the
  endpoint is not advertised. If it ever needs closing, a shared secret in a
  header checked against an environment variable is the smallest thing that
  works, and it goes in `route()`.
- **No deletion, no retention policy.** Finished recordings stay until something
  else removes them.
- **No transcription, no database.** It is a place for bytes to land.
