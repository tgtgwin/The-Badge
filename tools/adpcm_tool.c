/* The firmware's ADPCM codec, driven from a test.
 *
 * 🚨 This is not part of the firmware and not part of the simulator. It exists
 *    so the recording chain can be walked end to end on a laptop, which is the
 *    only place it can be walked at all: tools/serve/test_roundtrip.py compiles
 *    this against main/adpcm.c, encodes real PCM with it, uploads the result
 *    through the real protocol, and decodes what the server hands back.
 *
 *    Without it, the two ways this code fails — a header four bytes short, and a
 *    decoder whose shift order is wrong — are both found by recording something
 *    on the badge and listening to it. The second one comes back as noise that
 *    still sounds like speech, which is a miserable afternoon.
 *
 *    It links main/adpcm.c unchanged. A copy of the encoder that the test could
 *    pass would prove nothing.
 *
 *   adpcm_tool encode <in.s16le> <out.adpcm>
 *   adpcm_tool decode <in.adpcm> <out.s16le> [max_samples]
 *   adpcm_tool header <data_bytes>          (the 60 bytes, as hex)
 */
#include "adpcm.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t *slurp(const char *path, size_t *n)
{
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (len < 0) { perror(path); exit(2); }
    uint8_t *buf = malloc((size_t)len ? (size_t)len : 1);
    if (len && fread(buf, 1, (size_t)len, f) != (size_t)len) { perror(path); exit(2); }
    fclose(f);
    *n = (size_t)len;
    return buf;
}

static void spit(const char *path, const void *p, size_t n)
{
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); exit(2); }
    if (n && fwrite(p, 1, n, f) != n) { perror(path); exit(2); }
    fclose(f);
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: adpcm_tool encode|decode|header ...\n");
        return 2;
    }

    if (!strcmp(argv[1], "header")) {
        if (argc < 3) return 2;
        uint32_t data = (uint32_t)strtoul(argv[2], NULL, 0);
        uint8_t h[ADPCM_WAV_HEADER_BYTES];
        size_t n = adpcm_wav_header(h, data, ADPCM_SAMPLE_RATE);
        for (size_t i = 0; i < n; i++) printf("%02x", h[i]);
        printf("\n");
        return 0;
    }

    if (!strcmp(argv[1], "encode")) {
        if (argc < 4) return 2;
        size_t n = 0;
        uint8_t *raw = slurp(argv[2], &n);
        size_t samples = n / 2;
        int blocks = (int)((samples + ADPCM_BLOCK_SAMPLES - 1) / ADPCM_BLOCK_SAMPLES);
        uint8_t *out = calloc((size_t)blocks ? (size_t)blocks : 1, ADPCM_BLOCK_BYTES);
        const int16_t *pcm = (const int16_t *)raw;

        /* 🚨 One block at a time, exactly as mic_task() does it. The predictor is
         *    reset by the block header and the step index is not, and that
         *    asymmetry is the thing a test that encoded the whole buffer in one
         *    call would not exercise. */
        for (int b = 0; b < blocks; b++) {
            size_t off = (size_t)b * ADPCM_BLOCK_SAMPLES;
            int avail = (int)(samples - off);
            if (avail > ADPCM_BLOCK_SAMPLES) avail = ADPCM_BLOCK_SAMPLES;
            if (avail < 0) avail = 0;
            adpcm_encode_block(avail ? pcm + off : pcm, avail,
                               out + (size_t)b * ADPCM_BLOCK_BYTES);
        }
        spit(argv[3], out, (size_t)blocks * ADPCM_BLOCK_BYTES);
        fprintf(stderr, "encoded %zu samples into %d blocks (%d bytes)\n",
                samples, blocks, blocks * ADPCM_BLOCK_BYTES);
        free(raw); free(out);
        return 0;
    }

    if (!strcmp(argv[1], "decode")) {
        if (argc < 4) return 2;
        size_t n = 0;
        uint8_t *in = slurp(argv[2], &n);
        int blocks = (int)(n / ADPCM_BLOCK_BYTES);
        int limit = (argc > 4) ? atoi(argv[4]) : blocks * ADPCM_BLOCK_SAMPLES;
        int total = blocks * ADPCM_BLOCK_SAMPLES;
        if (limit < total) total = limit;
        int16_t *pcm = calloc((size_t)total ? (size_t)total : 1, sizeof *pcm);

        int done = 0;
        for (int b = 0; b < blocks && done < total; b++) {
            int want = total - done;
            if (want > ADPCM_BLOCK_SAMPLES) want = ADPCM_BLOCK_SAMPLES;
            adpcm_decode_block(in + (size_t)b * ADPCM_BLOCK_BYTES, pcm + done, want);
            done += want;
        }
        spit(argv[3], pcm, (size_t)done * 2);
        fprintf(stderr, "decoded %d blocks into %d samples\n", blocks, done);
        free(in); free(pcm);
        return 0;
    }

    fprintf(stderr, "unknown command %s\n", argv[1]);
    return 2;
}
