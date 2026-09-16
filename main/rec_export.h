/* The window used for taking recordings off the badge.
 *
 * 🚨 Only this window is opened, to keep rec_store.c's internals (the ring, the
 * absolute offsets, the two directory copies) from leaking out. The USB side
 * only needs to know how many there are and where each one starts and how long
 * it is.
 *
 * 🚨 `off` is an **absolute** offset on the ring, which is why it is 64-bit.
 * It used to be a partition offset that fitted in 32 bits, but the ring's write
 * head is monotonic: at 8 KB/s it passes 4 GB after about 137 hours of
 * recording, and a truncated offset would then silently read the wrong place.
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* The fake FAT's root directory is one 4 KB sector holding 16 entries, so the
 * export window cannot offer more than that however many are stored. Uploading
 * is the path that is not limited; this is the cable-in-hand fallback. */
#define REC_EXPORT_MAX 12

/* The stored format. Used as-is when making up the WAV header —
 * 🚨 if these values disagree with what is actually stored, the file opens but
 * the sound is broken.
 * 🚨 Aliases of adpcm.h's, not values. They used to be written out again here,
 *    which made four copies of the sample rate in the firmware alone; the names
 *    stay because the export side reads better with them, but there is now one
 *    place a change has to be made. tools/regress.sh checks that all four agree,
 *    and that tools/serve agrees too. */
#include "adpcm.h"
#define REC_SAMPLE_RATE         ADPCM_SAMPLE_RATE
#define REC_ADPCM_BLOCK_BYTES   ADPCM_BLOCK_BYTES
#define REC_ADPCM_BLOCK_SAMPLES ADPCM_BLOCK_SAMPLES

typedef struct {
    uint64_t off;        /* data start, absolute on the ring */
    uint32_t bytes;      /* ADPCM bytes */
    int64_t  started;    /* unix seconds; 0 if the clock was not set */
} rec_export_t;

/* Only finished recordings (one in progress is left out). Returns the count. */
int  rec_export_list(rec_export_t *out, int max);
/* Reads `len` bytes from an absolute offset. */
bool rec_export_read(uint64_t off, void *dst, uint32_t len);
