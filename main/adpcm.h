#pragma once
#include <stdint.h>
#include <stddef.h>

/* An IMA ADPCM encoder, producing standard WAV blocks —
 * a custom format would mean bolting a decoder onto the server, whereas both
 * ffmpeg and Deepgram read this one directly.
 *
 * One block = 256 bytes = 4 header + 252 code bytes
 *           = 1 first sample + 504 = 505 samples
 * At 16 kHz that is 31.7 blocks or 8,110 bytes a second, about a quarter of raw PCM. */
#define ADPCM_BLOCK_BYTES   256
#define ADPCM_BLOCK_SAMPLES 505

/* 🚨 RIFF + fmt(20) + fact + data = 60 bytes, not 44 and not 48. A compressed
 * format carries samples-per-block and a fact chunk that PCM does not, so both
 * of the shorter numbers that look plausible are wrong. */
#define ADPCM_WAV_HEADER_BYTES 60

/* 🚨 The sample rate, defined once, here, because there were four copies of it.
 *    rec_store.c had its own, audio_dsp.c had a float one, rec_export.h had a
 *    fourth called REC_SAMPLE_RATE, and tools/serve had a fifth on the other
 *    side of the wire. Four of them agreeing is a coincidence that lasts until
 *    somebody changes one — and a sample rate that disagrees does not fail, it
 *    plays everything at the wrong speed, which sounds like a bad microphone
 *    rather than a bad constant. */
#define ADPCM_SAMPLE_RATE      16000

/* Turns 505 pcm samples into one block (256 B). Short input is padded with zeros. */
void adpcm_encode_block(const int16_t *pcm, int n, uint8_t out[ADPCM_BLOCK_BYTES]);

/* The exact inverse, for playing a recording back on the badge.
 * 🚨 Decoding a block does not need the ones before it: the predictor and the
 * step index are both in the block header, which is what keeps one damaged
 * block from taking the rest of the recording with it. */
void adpcm_decode_block(const uint8_t in[ADPCM_BLOCK_BYTES], int16_t *pcm, int n);

/* Builds a WAV header so the format can be read. Returns the header length.
 * 🚨 The one implementation. The firmware and tools/serve both use it — there
 * used to be a second, shorter copy in usb_export.c that was four bytes out. */
size_t adpcm_wav_header(uint8_t *out, uint32_t data_bytes, uint32_t sample_rate);
