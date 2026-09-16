/* What the rest of the firmware may know about the recording store.
 *
 * 🚨 This header did not exist until the uploader arrived, and its absence is
 * why `rec_mark_sent` and `rec_oldest_unsent` sat in rec_store.c calling
 * nothing: there was no way for another file to reach them. Every one of those
 * functions was written for an uploader that was never committed.
 *
 * The store keeps two absolute byte counters and nothing else that matters
 * outside. An entry is a window on the ring — who owns it is decided by
 * comparing it against `uploaded_abs`, never by a flag inside it.
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define REC_MAX 24
#define REC_SESSION_LEN 17      /* 16 hex chars + NUL */

/* One recording, as seen from outside. */
typedef struct {
    uint64_t start_abs;         /* where it begins on the ring */
    uint32_t bytes;             /* encoded bytes written */
    int64_t  started;           /* unix seconds; 0 if the clock was not set */
    uint8_t  lang;              /* 0 = ko, 1 = en */
    bool     done;              /* did it close cleanly? */
    bool     uploaded;          /* is it entirely below uploaded_abs? */
    char     session[REC_SESSION_LEN];   /* upload session, empty if never started */
} rec_info_t;

/* ── the ring ────────────────────────────────────────────────
 * 🚨 The one invariant that must never break: uploaded_abs <= write_abs,
 *    and write_abs - uploaded_abs <= the size of the data area. Everything
 *    else — how much is free, whether a recording may start, what may be
 *    overwritten — is derived from those two numbers. */
uint64_t rec_write_abs(void);
uint64_t rec_uploaded_abs(void);
/* Size of the ring itself, in bytes. */
uint32_t rec_data_size(void);

/* How much is waiting to go out: write_abs - uploaded_abs. */
uint32_t rec_pending_bytes(void);

/* Walks the finished recordings that are not yet uploaded, oldest first.
 * `idx` starts at 0 and is bumped by the caller. false when there are none left. */
bool rec_next_unsent(int *idx, rec_info_t *out);

/* Reads `len` bytes starting `off` bytes into the recording at `start_abs`.
 * false if it would run past the end of that recording or past write_abs. */
bool rec_read_recording(uint64_t start_abs, uint32_t off, void *dst, uint32_t len);

/* Records that everything below `abs` reached the server, and drops the
 * recordings that are now entirely below it. 🚨 Never call this while
 * recording — an entry index is a position in the list, so removing an
 * earlier one moves the microphone's target. */
void rec_mark_uploaded(uint64_t abs);

/* Persists the upload session id on a recording, so a power cut in the middle
 * of an upload does not orphan the half-written file on the server. */
void rec_set_session(uint64_t start_abs, const char *session);

/* Absolute offset of the end of the newest recording (== write_abs when idle). */
uint64_t rec_tail_abs(void);
