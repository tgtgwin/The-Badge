/* Microphone conditioning, applied to the raw PCM **before** it is encoded.
 *
 * 🚨 Order matters more than anything else in here. A DC offset eats the ADPCM
 *    step range, so the same speech comes out coarser for no reason; and every
 *    bit the noise gate trims is a bit ADPCM does not have to spend. Filter
 *    first, compress second.
 *
 * Everything is float: the ESP32-S3 has an FPU, and at 16 kHz this runs about
 * ten operations a sample — 160k a second, which is nothing next to a 240 MHz
 * core. Cheaper integer maths here would save nothing measurable and cost the
 * ability to tune by ear, which is the only way any of these numbers get
 * settled. See tools/regress.sh for what may not be changed.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/* ── the numbers, all of which want checking on a real microphone ─────
 * These are starting points for a noisy meeting room, not measurements. The
 * setters below exist so they can be moved without a rebuild while tuning. */

/* Hardware gain, in dB. 🚨 Lowered from the 30 dB this used to sit at: with
 * AGC downstream, a front end already pushed to the rails leaves the AGC
 * nothing to do but pull it back down, and the clipping is already in the
 * samples by then. 18 dB leaves headroom for someone speaking up. */
#define MIC_GAIN_DB             18.0f

/* DC blocker. One pole at 100 Hz — below the fundamental of a male voice, so
 * it removes the offset and the rumble without touching speech. */
#define DSP_HP_HZ               100.0f

/* AGC. Target is -18 dBFS: loud enough to be clear, far enough from full
 * scale that a laugh or a door slam does not clip. */
#define DSP_AGC_TARGET_DBFS     (-18.0f)
#define DSP_AGC_ATTACK_MS       10.0f    /* catch a syllable before it is gone */
#define DSP_AGC_RELEASE_MS      500.0f   /* but let go slowly, or it pumps */
#define DSP_AGC_MIN             0.25f
#define DSP_AGC_MAX             4.0f

/* Noise gate. 12 dB of cut below the threshold, applied gradually — a hard
 * gate on speech sounds like the room breathing in and out. */
#define DSP_GATE_DBFS           (-45.0f)
#define DSP_GATE_CUT_DB         12.0f
#define DSP_GATE_HOLD_MS        200.0f

typedef struct {
    /* high pass */
    float hp_x1, hp_y1;
    /* automatic gain */
    float agc_env;      /* envelope follower, in sample units */
    float agc_gain;     /* current gain, clamped to [MIN, MAX] */
    /* noise gate */
    float gate_env;
    /* level of the block just processed, 0..1 of full scale — for the meter */
    float level;
} dsp_state_t;

/* Clears the filter memory and parks the gain at 1.0. Call before every
 * recording: the state carries across blocks on purpose, and a stale envelope
 * from the last meeting would open the first seconds at the wrong gain. */
void dsp_reset(dsp_state_t *st);

/* In place, 16-bit mono. Safe to call with any block size; the state is
 * per-sample so blocks may be any length. */
void dsp_process(dsp_state_t *st, int16_t *pcm, size_t samples);

/* How loud the block just processed was, 0..1 of full scale.
 *
 * 🚨 It is measured on the way **out**, after the gain and the gate, because
 *    that is what the recording will contain. A meter tapped on the way in
 *    reads healthy while the AGC is pulling everything down, or while the gate
 *    is shutting it out entirely — which is the one fault a meter exists to
 *    show you.
 * 🚨 It is a mean over the block, not a peak. A peak meter on speech flickers
 *    on every consonant and settles on nothing, and what a person holding the
 *    badge is asking is "is it hearing me at all", not "what was the loudest
 *    millisecond". */
float dsp_level(const dsp_state_t *st);

/* ── runtime tuning ─────────────────────────────────────────
 * For settling the values on real hardware. Anything set here applies from the
 * next block and is deliberately **not** persisted: a tuning session should not
 * survive a reboot and quietly become the shipped default. */
void dsp_set_gain_target_dbfs(float dbfs);   /* -30 .. -6 */
void dsp_set_gate_dbfs(float dbfs);          /* -70 .. -20, or -99 to disable */
void dsp_set_agc_enabled(bool on);
float dsp_get_gain_target_dbfs(void);
float dsp_get_gate_dbfs(void);
bool dsp_get_agc_enabled(void);
