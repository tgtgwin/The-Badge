/* The recording store. The badge records on its own into flash.
 *
 * The point is using it in a meeting room with no phone and no WiFi, so
 * nothing is streamed. The original is always in flash first and getting it
 * off is a later problem — there is nothing to lose to a dropped connection.
 *
 * Partition layout (rec, 24 MB):
 *   0x0000..0x0FFF  directory copy A
 *   0x1000..0x1FFF  directory copy B
 *   0x2000..        data, a ring of bytes
 *
 * 🚨 The data area is a **ring**, not an append-only file. Append-only was
 * right while the only way out was a USB cable and 51 minutes was the whole
 * point. It is wrong now: eight hours is 234 MB, so the badge has to hand
 * recordings over as it goes, and space must come back the moment the server
 * confirms a chunk.
 *
 * The old alloc_off() took the highest end in the list, so an already-exported
 * recording still holding its slot kept the watermark pinned there. That is
 * why a 24 MB area once showed 3.9 minutes left with all twelve recordings
 * taken off — and why, with nothing calling the code that removed them from
 * the list, the area ran out once and never came back.
 *
 * There is no filesystem because audio is sequential: a FAT here would add
 * overhead and fragmentation and nothing else.
 *
 * 🚨 The two absolute counters are the whole model:
 *      write_abs     everything below this has been recorded (monotonic)
 *      uploaded_abs  everything below this reached the server (monotonic)
 *    Occupancy is write_abs - uploaded_abs. A recording is "gone" when it falls
 *    entirely below uploaded_abs — there is no per-entry sent flag any more,
 *    because a flag has to be written for every entry while a counter has to be
 *    written once.
 */
#include "port.h"
#include "adpcm.h"
#include "audio_dsp.h"
#include "rec_store.h"
#include "rec_export.h"
#include "esp_partition.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_codec_dev.h"
#include "bsp/esp32_s3_touch_amoled_1_75c.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stddef.h>
#include <time.h>
#include <math.h>       /* log10f, for the meter — see level_of() */

static const char *TAG = "rec";

#define REC_SUBTYPE   0x40
#define SECTOR        4096
#define DIR_BYTES     4096                       /* one sector per copy */
#define DIR_A         0
#define DIR_B_OFF     SECTOR                     /* 0x1000 */
#define DATA_START    (SECTOR * 2)               /* 0x2000 */
/* 🚨 The rate comes from adpcm.h — see the note there about the four copies. */
#define SAMPLE_RATE   ADPCM_SAMPLE_RATE
#define REC_MAGIC     0x43455242u                /* "BREC" */
/* Bumped when the directory or entry layout changes. 🚨 Without this a badge
 * that was upgraded reads the old bytes as if they were the new structure and
 * reports nonsense — the magic alone cannot tell 24-byte entries from 40-byte
 * ones. An older version is discarded, which loses the recordings on the badge
 * but not on the server. */
#define REC_VERSION   2

#define SESSION_NONE  ""

typedef struct {
    uint64_t start_abs;      /* where this recording begins on the ring */
    uint32_t bytes;          /* encoded bytes written */
    int64_t  started;        /* unix seconds; 0 if the clock was not set */
    uint8_t  lang;           /* 0=ko 1=en */
    uint8_t  done;           /* did it close cleanly? */
    uint8_t  pad[2];
    char     session[REC_SESSION_LEN];  /* upload session id, "" if never set */
} rec_ent_t;

typedef struct {
    uint32_t  version;
    uint32_t  seq;           /* bumped on every save; the newer copy wins */
    uint32_t  crc;           /* over everything after this field */
    uint64_t  write_abs;
    uint64_t  uploaded_abs;  /* throttled: only written every few MB */
    uint32_t  count;
    rec_ent_t ent[REC_MAX];
} rec_dir_t;

static const esp_partition_t *s_part;
static rec_dir_t  s_dir;
static bool       s_ready;

/* Which copy was written last, so the next save goes to the other one. */
static int s_last_copy = -1;

/* State while recording */
static volatile bool s_active;
static volatile bool s_stop_req;
/* 🚨 Paused is not stopped. The microphone stays open and keeps being read —
 * closing and reopening I2S to pause would drop a few hundred milliseconds each
 * time and leave a click in the middle of the file, and it would also take the
 * meter down, which is the one thing that tells you the microphone is still
 * there. What stops is the encoding and the writing. */
static volatile bool s_paused;
static int64_t       s_pause_began_us;    /* 0 when not paused */
static int64_t       s_paused_total_us;   /* paused time so far, for the clock */
static volatile int  s_level;             /* 0..100, for the meter */
static int           s_slot = -1;
static uint32_t      s_wrote;        /* bytes written by this recording */
static uint64_t      s_write_abs;    /* write head for this recording */
static uint64_t      s_erased_abs;   /* everything below this is erased */
static uint8_t      *s_page;         /* written 4 KB at a time */
static int           s_page_len;
static int64_t       s_started_us;

/* Microphone conditioning. Lives here rather than in mic_task because the
 * filter memory has to carry across blocks — resetting per block would put a
 * step in the middle of every 31 ms. */
static dsp_state_t   s_dsp;

/* ── a small CRC32 ────────────────────────────────────────────
 * Hand-rolled rather than pulling in esp_crc.h: it is fifteen lines and the
 * directory is the one place where a wrong answer silently destroys data. */
static uint32_t crc32_of(const void *data, size_t len)
{
    const uint8_t *p = data;
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++) c = (c >> 1) ^ (0xEDB88320u & (~(c & 1) + 1));
    }
    return c ^ 0xFFFFFFFFu;
}

/* ── the ring ───────────────────────────────────────────────── */

static uint32_t data_size(void)
{
    if (!s_part || s_part->size <= DATA_START) return 0;
    /* 🚨 A multiple of SECTOR, or a sector-aligned absolute offset would not
     * land on a sector boundary once the ring wraps. It is, because both the
     * partition size and DATA_START are. */
    return s_part->size - DATA_START;
}

static uint32_t phys_of(uint64_t abs)
{
    uint32_t d = data_size();
    if (!d) return 0;
    return DATA_START + (uint32_t)(abs % d);
}

static uint64_t align_up_sector(uint64_t v)
{
    return (v + SECTOR - 1) & ~(uint64_t)(SECTOR - 1);
}

/* ── the directory ────────────────────────────────────────────
 * 🚨 Two copies, written alternately. The old single-copy save erased the
 * sector and then wrote it, and a power cut in that window left no directory
 * at all — the checksum failed and every recording on the badge became
 * unreachable in one go. Alternating means the other copy is always intact. */
static uint32_t dir_slot_off(int copy)
{
    return copy == 0 ? DIR_A : DIR_B_OFF;
}

static void dir_save(void)
{
    if (!s_part) return;

    int next = (s_last_copy == 0) ? 1 : 0;      /* -1 (never saved) goes to A */
    s_dir.version = REC_VERSION;
    s_dir.seq++;
    s_dir.crc = 0;
    s_dir.crc = crc32_of(&s_dir.write_abs, sizeof s_dir - offsetof(rec_dir_t, write_abs));

    uint32_t off = dir_slot_off(next);
    if (esp_partition_erase_range(s_part, off, DIR_BYTES) != ESP_OK) return;
    if (esp_partition_write(s_part, off, &s_dir, sizeof s_dir) != ESP_OK) return;
    s_last_copy = next;
}

static bool dir_read_copy(int copy, rec_dir_t *out)
{
    if (esp_partition_read(s_part, dir_slot_off(copy), out, sizeof *out) != ESP_OK) return false;
    if (out->version != REC_VERSION) return false;
    uint32_t want = out->crc;
    if (crc32_of(&out->write_abs, sizeof *out - offsetof(rec_dir_t, write_abs)) != want) return false;
    if (out->count > REC_MAX) return false;
    if (out->uploaded_abs > out->write_abs) return false;
    return true;
}

static void purge_uploaded(void);

static void dir_load(void)
{
    if (s_ready) return;
    s_ready = true;
    s_part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, REC_SUBTYPE, "rec");
    if (!s_part) { ESP_LOGW(TAG, "no recording partition — the table needs reflashing"); return; }

    rec_dir_t a, b;
    bool oka = dir_read_copy(0, &a);
    bool okb = dir_read_copy(1, &b);

    if (oka && okb)      { s_dir = (a.seq >= b.seq) ? a : b; s_last_copy = (a.seq >= b.seq) ? 0 : 1; }
    else if (oka)        { s_dir = a; s_last_copy = 0; }
    else if (okb)        { s_dir = b; s_last_copy = 1; }
    else {
        ESP_LOGI(TAG, "creating a fresh directory");
        memset(&s_dir, 0, sizeof s_dir);
        dir_save();
    }

    ESP_LOGI(TAG, "recording area %lu KB, %lu stored, %u not yet taken off, %lu KB waiting",
             (unsigned long)(s_part->size / 1024), (unsigned long)s_dir.count,
             (unsigned)port_rec_pending(),
             (unsigned long)(rec_pending_bytes() / 1024));

    /* 🚨 Everything below the write head was erased by whoever left it there.
     * Starting the erase cursor mid-sector would erase a sector that has
     * already been written and destroy the tail of the newest recording. */
    s_erased_abs = align_up_sector(s_dir.write_abs);

    purge_uploaded();
}

/* Drops the recordings that are now entirely below the uploaded head.
 *
 * 🚨 Never called while recording. s_slot is an index into this array, so
 * removing an earlier entry makes the microphone write into the wrong one.
 *
 * The bytes are not erased here — erasing 17 MB on the spot would hold the
 * flash for over forty seconds, and the next recording erases sector by sector
 * just before writing anyway. Leaving the list is what makes the space
 * available. */
static void purge_uploaded(void)
{
    if (s_active) return;
    uint32_t keep = 0;
    for (uint32_t i = 0; i < s_dir.count; i++) {
        const rec_ent_t *e = &s_dir.ent[i];
        bool whole = e->done && (e->start_abs + e->bytes <= s_dir.uploaded_abs);
        if (whole) continue;
        if (keep != i) s_dir.ent[keep] = s_dir.ent[i];
        keep++;
    }
    if (keep == s_dir.count) return;
    uint32_t gone = s_dir.count - keep;
    s_dir.count = keep;
    memset(&s_dir.ent[keep], 0, sizeof(rec_ent_t) * (REC_MAX - keep));
    dir_save();
    ESP_LOGI(TAG, "removed %u uploaded recordings — %lu s now available",
             (unsigned)gone, (unsigned long)port_rec_free_seconds());
}

size_t port_rec_capacity(void)
{
    dir_load();
    return s_part ? s_part->size : 0;
}

int port_rec_pending(void)
{
    dir_load();
    int n = 0;
    for (uint32_t i = 0; i < s_dir.count; i++)
        if (s_dir.ent[i].done && (s_dir.ent[i].start_abs + s_dir.ent[i].bytes > s_dir.uploaded_abs)) n++;
    return n;
}

uint32_t port_rec_free_seconds(void)
{
    dir_load();
    if (!s_part) return 0;
    uint32_t d = data_size();
    uint32_t used = rec_pending_bytes();
    if (used >= d) return 0;
    /* 🚨 Dividing (16000 / 505) first truncates 31.68 to 31 in integers,
     * which turns 8,111 bytes a second into 7,936 and makes the remaining
     * time 2.2% too generous (51.7 minutes in a 24 MB area showed as 52.9).
     * Multiply first to keep the digits. */
    uint64_t left = (uint64_t)(d - used) * ADPCM_BLOCK_SAMPLES;
    return (uint32_t)(left / ((uint64_t)ADPCM_BLOCK_BYTES * SAMPLE_RATE));
}

/* ── writing ──────────────────────────────────────────────── */

/* Erases every sector between the cursor and `abs_end` (sector aligned). */
static bool erase_upto(uint64_t abs_end)
{
    while (s_erased_abs < abs_end) {
        uint32_t phys = phys_of(s_erased_abs);
        if (esp_partition_erase_range(s_part, phys, SECTOR) != ESP_OK) {
            ESP_LOGE(TAG, "erase failed @0x%lX", (unsigned long)phys);
            return false;
        }
        s_erased_abs += SECTOR;
    }
    return true;
}

/* Writes `len` bytes at absolute `abs`, splitting at the end of the ring.
 * A 4 KB page that straddles the wrap point is the reason this is not a single
 * esp_partition_write. */
static bool ring_write(uint64_t abs, const uint8_t *src, uint32_t len)
{
    uint32_t d = data_size();
    if (!d || len > d) return false;
    uint32_t phys = phys_of(abs);
    uint32_t first = d - (phys - DATA_START);
    if (first > len) first = len;

    if (esp_partition_write(s_part, phys, src, first) != ESP_OK) return false;
    if (len > first) {
        if (esp_partition_write(s_part, DATA_START, src + first, len - first) != ESP_OK) return false;
    }
    return true;
}

static bool page_flush(void)
{
    if (!s_page_len) return true;

    if (!erase_upto(align_up_sector(s_write_abs + s_page_len))) return false;
    if (!ring_write(s_write_abs, s_page, s_page_len)) return false;

    s_write_abs += s_page_len;
    s_wrote     += s_page_len;
    s_page_len = 0;
    return true;
}

static bool put_block(const uint8_t *b)
{
    memcpy(s_page + s_page_len, b, ADPCM_BLOCK_BYTES);
    s_page_len += ADPCM_BLOCK_BYTES;
    if (s_page_len >= SECTOR) return page_flush();
    return true;
}

/* Linear block level to a 0-100 meter reading, through dB.
 * 🚨 The mapping is the difference between a meter that means something and one
 *    that does not. Speech at a comfortable volume sits near -20 dBFS, which is
 *    a tenth of full scale: on a linear bar every level anybody will ever
 *    produce is inside the first ten percent, and the other ninety are reserved
 *    for levels that only happen when somebody drops the badge on the table.
 *    -60 dBFS at the bottom is below the noise floor of any room this is used
 *    in, so the bar moves for everything that matters and agrees with the ear. */
static int level_of(float lin)
{
    if (lin <= 0.0f) return 0;
    float db = 20.0f * log10f(lin);
    if (db <= -60.0f) return 0;
    if (db >=   0.0f) return 100;
    return (int)((db + 60.0f) * (100.0f / 60.0f));
}

/* ── the microphone ─────────────────────────────────────── */

/* The microphone handle. bsp_audio_codec_microphone_init() allocates i2c_ctrl,
 * es7210 and codec_dev afresh on every call, and the BSP offers no way to
 * undo that — so creating one per recording simply leaks. It is created once
 * and kept.
 * What actually consumes resources is open/close (the I2S channel), and that
 * is done per recording. */
static esp_codec_dev_handle_t s_mic;

/* How often the write head and the uploaded head are written to the directory.
 * 🚨 The uploaded head MUST be throttled. It advances every chunk, and each
 * advance would otherwise erase and rewrite a whole directory sector — at 32 KB
 * a chunk that is an erase every four seconds, which wears the directory out in
 * weeks while the data area lasts ten years. */
#define DIR_FLUSH_BYTES (1024u * 1024u)

static uint64_t s_dir_flushed_uploaded;

static void mic_task(void *arg)
{
    (void)arg;
    bool s_opened = false;
    if (!s_mic) s_mic = bsp_audio_codec_microphone_init();
    esp_codec_dev_handle_t mic = s_mic;
    if (!mic) {
        ESP_LOGE(TAG, "microphone init failed");
        goto bail;
    }
    esp_codec_dev_sample_info_t fs = {
        .sample_rate = SAMPLE_RATE, .channel = 1, .bits_per_sample = 16,
    };
    if (esp_codec_dev_open(mic, &fs) != ESP_OK) {
        ESP_LOGE(TAG, "microphone open failed");
        goto bail;
    }
    s_opened = true;
    esp_codec_dev_set_in_gain(mic, MIC_GAIN_DB);

    const int CHUNK = ADPCM_BLOCK_SAMPLES;      /* 505 samples = one block */
    int16_t *pcm = heap_caps_malloc(CHUNK * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    uint8_t  blk[ADPCM_BLOCK_BYTES];
    int64_t  last_dir = esp_timer_get_time();

    if (!pcm) { ESP_LOGE(TAG, "no buffer"); goto bail; }

    /* Samples go missing if the CPU sleeps while I2S is reading the microphone */
    port_pm_hold(true);
    ESP_LOGI(TAG, "recording started (slot %d, abs %llu)", s_slot,
             (unsigned long long)s_dir.ent[s_slot].start_abs);
    port_heap_report("rec-start");
    while (!s_stop_req) {
        if (esp_codec_dev_read(mic, pcm, CHUNK * 2) != ESP_OK) break;

        /* 🚨 The microphone is read whether or not recording is paused, and the
         *    conditioning runs either way. It carries filter memory, so skipping
         *    blocks would leave a step in the signal at the seam; and the level
         *    is what tells a person the badge is still alive — a paused
         *    recording with a dead meter looks like a recording that has failed.
         *    The encoder and the flash write are what pausing stops, and those
         *    are the two things a pause is for. */
        dsp_process(&s_dsp, pcm, CHUNK);
        int now_level = level_of(dsp_level(&s_dsp));
        /* Fast to rise, slow to fall — a bar that drops as quickly as it climbs
         * is a flicker at thirty-one blocks a second, not a meter. */
        s_level = (now_level > s_level) ? now_level
                                        : s_level - (s_level - now_level + 3) / 4;
        if (s_paused) continue;

        adpcm_encode_block(pcm, CHUNK, blk);
        if (!put_block(blk)) break;

        /* Rewrite the directory every 30 s so a sudden power loss keeps this much.
         * It is throttled rather than written per block for the same wear reason
         * as the uploaded head. */
        int64_t now = esp_timer_get_time();
        if (now - last_dir > 30000000LL) {
            last_dir = now;
            s_dir.ent[s_slot].bytes = s_wrote;
            dir_save();
            port_battery_log("rec");
        }
        /* Stop by itself when the ring has nowhere left to put the next page */
        if (rec_pending_bytes() + SECTOR > data_size()) {
            ESP_LOGW(TAG, "recording area full — stopping");
            break;
        }
    }

    page_flush();
    s_dir.ent[s_slot].bytes = s_wrote;
    s_dir.ent[s_slot].done  = 1;
    s_dir.write_abs = s_write_abs;
    dir_save();

    esp_codec_dev_close(mic);       /* give the I2S channel back; the handle is reused */
    heap_caps_free(pcm);
    /* Recording finished, so give the 4 KB write buffer back too. Reallocated next time. */
    if (s_page) { heap_caps_free(s_page); s_page = NULL; s_page_len = 0; }
    ESP_LOGI(TAG, "recording ended, %lu bytes (%lu s)",
             (unsigned long)s_wrote,
             (unsigned long)(s_wrote / ((SAMPLE_RATE / ADPCM_BLOCK_SAMPLES) * ADPCM_BLOCK_BYTES)));
    port_pm_hold(false);
    s_active = false;
    port_heap_report("rec-end");     /* should match the start */
    vTaskDelete(NULL);
    return;

bail:
    /* Whatever failed, give everything back on the way out. The slot is
     * collapsed too, so an empty recording does not appear in the list —
     * otherwise zero-byte recordings pile up that can never be exported. */
    if (s_opened) esp_codec_dev_close(mic);
    if (s_page) { heap_caps_free(s_page); s_page = NULL; s_page_len = 0; }
    if (s_slot >= 0 && s_dir.count > 0 && s_slot == (int)s_dir.count - 1) {
        s_dir.count--;
        /* The bytes that were claimed go back: the ring only knows the write
         * head, so an abandoned recording must rewind it. */
        dir_save();
    }
    s_active = false;
    vTaskDelete(NULL);
}

bool port_rec_start(int lang)
{
    dir_load();
    if (!s_part || s_active) return false;
    if (port_rec_free_seconds() < 60) { ESP_LOGW(TAG, "less than a minute of space left"); return false; }
    if (!data_size()) return false;

    /* 🚨 The oldest entries are only expendable once they are on the server.
     * With the ring this is not a nicety: a new recording writes over whatever
     * is at the write head, so starting one that would swallow un-uploaded
     * audio would silently destroy a meeting that was never handed over. */
    while (s_dir.count >= REC_MAX) {
        const rec_ent_t *e = &s_dir.ent[0];
        if (e->start_abs + e->bytes > s_dir.uploaded_abs) {
            ESP_LOGW(TAG, "list is full and the oldest is not uploaded — refusing");
            return false;
        }
        memmove(&s_dir.ent[0], &s_dir.ent[1], sizeof(rec_ent_t) * (s_dir.count - 1));
        s_dir.count--;
        memset(&s_dir.ent[s_dir.count], 0, sizeof(rec_ent_t));
    }

    if (!s_page) s_page = heap_caps_malloc(SECTOR, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_page) return false;

    s_slot = s_dir.count++;
    s_dir.ent[s_slot] = (rec_ent_t){
        .start_abs = s_dir.write_abs, .bytes = 0, .started = (int64_t)time(NULL),
        .lang = (uint8_t)(lang ? 1 : 0), .done = 0, .session = SESSION_NONE,
    };
    s_write_abs = s_dir.write_abs;
    s_wrote = 0;
    s_page_len = 0;
    s_erased_abs = align_up_sector(s_write_abs);
    s_started_us = esp_timer_get_time();
    /* 🚨 Cleared here rather than assumed: these outlive a recording, and a
     * stale paused_total would make the second recording's clock start short by
     * however long the first one was paused. */
    s_paused          = false;
    s_pause_began_us  = 0;
    s_paused_total_us = 0;
    s_level           = 0;
    dsp_reset(&s_dsp);
    dir_save();

    /* An open speaker codec fights over the same I2S. Take it down first. */
    port_tone_enable(false);

    s_stop_req = false;
    s_active = true;
    if (xTaskCreate(mic_task, "mic", 4096, NULL, 8, NULL) != pdPASS) {
        s_active = false;
        return false;
    }
    return true;
}

void port_rec_stop(void) { s_stop_req = true; }
bool port_rec_active(void) { return s_active; }

void port_rec_pause(bool on)
{
    if (!s_active) return;
    if (on == s_paused) return;
    if (on) {
        s_pause_began_us = esp_timer_get_time();
        s_paused = true;
        ESP_LOGI(TAG, "recording paused at %lu bytes", (unsigned long)s_wrote);
    } else {
        /* 🚨 Bank the time before clearing the flag, and only if it was actually
         * paused. A resume that arrives twice would otherwise subtract the same
         * stretch twice and the clock would run backwards. */
        if (s_pause_began_us) {
            s_paused_total_us += esp_timer_get_time() - s_pause_began_us;
            s_pause_began_us = 0;
        }
        s_paused = false;
        ESP_LOGI(TAG, "recording resumed");
    }
}

bool port_rec_paused(void) { return s_paused; }

int port_rec_level(void) { return s_active ? s_level : 0; }

uint32_t port_rec_seconds(void)
{
    if (!s_active) return 0;
    /* 🚨 The wall clock, minus however long it has been paused — the clock is
     * what the person watching it cares about, and this is the face of it. It is
     * deliberately not derived from s_wrote: that would drift against the wall
     * every time a write was held up, and the number on screen is the one
     * people compare against the meeting. */
    int64_t paused = s_paused_total_us;
    if (s_paused && s_pause_began_us) paused += esp_timer_get_time() - s_pause_began_us;
    int64_t ran = esp_timer_get_time() - s_started_us - paused;
    return (uint32_t)(ran > 0 ? ran / 1000000 : 0);
}

/* ── what the uploader uses (rec_store.h) ─────────────────── */

uint64_t rec_write_abs(void)    { dir_load(); return s_active ? s_write_abs : s_dir.write_abs; }
uint64_t rec_uploaded_abs(void) { dir_load(); return s_dir.uploaded_abs; }
uint32_t rec_data_size(void)    { dir_load(); return data_size(); }
uint64_t rec_tail_abs(void)     { return rec_write_abs(); }

uint32_t rec_pending_bytes(void)
{
    dir_load();
    if (!s_part) return 0;
    uint64_t w = s_active ? s_write_abs : s_dir.write_abs;
    return (uint32_t)(w - s_dir.uploaded_abs);
}

static void fill_info(const rec_ent_t *e, rec_info_t *out)
{
    out->start_abs = e->start_abs;
    out->bytes     = e->bytes;
    out->started   = e->started;
    out->lang      = e->lang;
    out->done      = e->done != 0;
    out->uploaded  = (e->start_abs + e->bytes) <= s_dir.uploaded_abs;
    memcpy(out->session, e->session, REC_SESSION_LEN);
    out->session[REC_SESSION_LEN - 1] = 0;
}

bool rec_next_unsent(int *idx, rec_info_t *out)
{
    dir_load();
    if (!idx || !out || !s_ready) return false;
    for (int i = *idx; i < (int)s_dir.count; i++) {
        const rec_ent_t *e = &s_dir.ent[i];
        if (!e->done || e->bytes == 0) continue;
        if ((e->start_abs + e->bytes) <= s_dir.uploaded_abs) continue;
        fill_info(e, out);
        *idx = i + 1;
        return true;
    }
    *idx = (int)s_dir.count;
    return false;
}

bool rec_read_recording(uint64_t start_abs, uint32_t off, void *dst, uint32_t len)
{
    dir_load();
    if (!s_part || !dst || !len || !s_ready) return false;

    const rec_ent_t *e = NULL;
    for (uint32_t i = 0; i < s_dir.count; i++) {
        if (s_dir.ent[i].start_abs == start_abs) { e = &s_dir.ent[i]; break; }
    }
    if (!e) return false;
    if (off > e->bytes || len > e->bytes - off) return false;

    /* 🚨 Reading is bounded by write_abs, not by the entry's length: the
     * uploader reads a finished recording, but a live one is still growing and
     * the ring must not hand out bytes that were never written. */
    uint64_t at = start_abs + off;
    if (at + len > rec_write_abs()) return false;

    uint8_t *p = dst;
    uint32_t left = len;
    while (left) {
        uint32_t d = data_size();
        if (!d) return false;
        uint32_t phys = phys_of(at);
        uint32_t run = d - (phys - DATA_START);
        if (run > left) run = left;
        if (esp_partition_read(s_part, phys, p, run) != ESP_OK) return false;
        p += run; at += run; left -= run;
    }
    return true;
}

void rec_mark_uploaded(uint64_t abs)
{
    dir_load();
    if (!s_ready) return;
    if (abs > s_dir.write_abs) abs = s_dir.write_abs;    /* never past what exists */
    if (abs <= s_dir.uploaded_abs) return;

    s_dir.uploaded_abs = abs;

    /* Throttled: see the note at DIR_FLUSH_BYTES. The in-memory value is
     * authoritative and is written out at the next chance, on the next
     * recording's 30 s tick, or when a recording ends. */
    if (abs - s_dir_flushed_uploaded >= DIR_FLUSH_BYTES) {
        s_dir_flushed_uploaded = abs;
        dir_save();
    }
    purge_uploaded();
}

void rec_set_session(uint64_t start_abs, const char *session)
{
    dir_load();
    if (!s_ready) return;
    for (uint32_t i = 0; i < s_dir.count; i++) {
        if (s_dir.ent[i].start_abs != start_abs) continue;
        strlcpy(s_dir.ent[i].session, session ? session : SESSION_NONE, REC_SESSION_LEN);
        dir_save();
        return;
    }
}

/* ── the window used for exporting (rec_export.h) ─────────────
 * 🚨 Only these two are exposed, to keep the internal structure in. The USB
 * side only needs to know how many there are, where each starts and how long
 * it is. */

int rec_export_list(rec_export_t *out, int max)
{
    if (!s_ready) return 0;
    int got = 0;
    for (uint32_t i = 0; i < s_dir.count && got < max; i++) {
        const rec_ent_t *e = &s_dir.ent[i];
        /* 🚨 Unfinished recordings are excluded. Their length is not settled,
         * and exporting one makes the host read past the end. */
        if (!e->done || e->bytes == 0) continue;
        out[got].off     = e->start_abs;      /* absolute: rec_export_read maps it */
        out[got].bytes   = e->bytes;
        out[got].started = e->started;
        got++;
    }
    return got;
}

bool rec_export_read(uint64_t off, void *dst, uint32_t len)
{
    if (!s_part || !dst || !len) return false;
    uint64_t at = off;
    if (at + len > rec_write_abs()) return false;

    uint8_t *p = dst;
    uint32_t left = len;
    while (left) {
        uint32_t d = data_size();
        if (!d) return false;
        uint32_t phys = phys_of(at);
        uint32_t run = d - (phys - DATA_START);
        if (run > left) run = left;
        if (esp_partition_read(s_part, phys, p, run) != ESP_OK) return false;
        p += run; at += run; left -= run;
    }
    return true;
}
