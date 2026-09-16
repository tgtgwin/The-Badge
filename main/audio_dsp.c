/* Microphone conditioning. See audio_dsp.h for why the order matters. */
#include "audio_dsp.h"
#include "adpcm.h"
#include <math.h>
#include <string.h>

/* 🚨 From adpcm.h, not a second copy — see the note there. A sample rate that
 * disagrees with the encoder's does not fail, it detunes the filter. */
#define SAMPLE_RATE ((float)ADPCM_SAMPLE_RATE)

/* A one-pole coefficient for a time constant in milliseconds. */
static float coef_ms(float ms)
{
    if (ms <= 0.0f) return 0.0f;
    return expf(-1.0f / (ms * 0.001f * SAMPLE_RATE));
}

/* dBFS to linear, where 1.0 is full scale (32768). */
static float dbfs_to_lin(float dbfs)
{
    return powf(10.0f, dbfs / 20.0f) * 32768.0f;
}

/* The high pass is a one-pole DC blocker:
 *     y[n] = a * (y[n-1] + x[n] - x[n-1])
 * Its -3 dB point sits at about (1-a)/(2*pi) * fs. */
#define TWO_PI 6.283185307f
static float hp_a(void) { return expf(-TWO_PI * DSP_HP_HZ / SAMPLE_RATE); }

/* Runtime-tunable, deliberately not persisted (see the header). */
static float s_target      = 4125.0f;      /* -18 dBFS */
static float s_gate_thresh = 184.0f;       /* -45 dBFS */
static float s_gate_floor  = 0.2512f;      /* -12 dB */
static bool  s_agc_on      = true;

/* Cached coefficients. Computed on first use rather than in a constructor
 * because they need expf/powf, and doing that per block would be silly. */
static bool  s_ready;
static float s_hp_a, s_agc_att, s_agc_rel, s_gate_c;
static float s_agc_slew;

static void ensure_ready(void)
{
    if (s_ready) return;
    s_ready      = true;
    s_hp_a       = hp_a();
    s_agc_att    = coef_ms(DSP_AGC_ATTACK_MS);
    s_agc_rel    = coef_ms(DSP_AGC_RELEASE_MS);
    s_gate_c     = coef_ms(DSP_GATE_HOLD_MS);
    /* The gain itself is moved by a slew rather than snapped, or a transient
     * would yank it and be audible as a click. */
    s_agc_slew   = coef_ms(20.0f);
}

void dsp_reset(dsp_state_t *st)
{
    if (!st) return;
    memset(st, 0, sizeof *st);
    st->agc_gain = 1.0f;
}

void dsp_process(dsp_state_t *st, int16_t *pcm, size_t samples)
{
    if (!st || !pcm || !samples) return;
    ensure_ready();

    /* Summed here and kept once per block; see dsp_level() in the header for why
     * this is on the way out and why it is a mean. */
    double acc = 0.0;

    for (size_t i = 0; i < samples; i++) {
        float x = (float)pcm[i];

        /* 1. DC and rumble out. */
        float y = s_hp_a * (st->hp_y1 + x - st->hp_x1);
        st->hp_x1 = x;
        st->hp_y1 = y;

        float mag = fabsf(y);

        /* 2. How loud is it lately? Fast up, slow down — the asymmetric
         * envelope is what keeps a single loud consonant from crushing the
         * words around it. */
        float ea = (mag > st->agc_env) ? s_agc_att : s_agc_rel;
        st->agc_env = ea * st->agc_env + (1.0f - ea) * mag;

        float g = 1.0f;
        if (s_agc_on) {
            float want = (st->agc_env > 1.0f) ? (s_target / st->agc_env) : DSP_AGC_MAX;
            if (want > DSP_AGC_MAX) want = DSP_AGC_MAX;
            if (want < DSP_AGC_MIN) want = DSP_AGC_MIN;
            st->agc_gain += (want - st->agc_gain) * (1.0f - s_agc_slew);
            g = st->agc_gain;
        }

        /* 3. Below the gate, ease the level down rather than cutting it. A hard
         * gate on speech is heard as the room breathing. */
        st->gate_env = s_gate_c * st->gate_env + (1.0f - s_gate_c) * mag;
        if (st->gate_env < s_gate_thresh) {
            float t = (s_gate_thresh > 0.0f) ? (st->gate_env / s_gate_thresh) : 0.0f;
            if (t > 1.0f) t = 1.0f;
            g *= s_gate_floor + (1.0f - s_gate_floor) * t;
        }

        float out = y * g;
        /* 🚨 Clamped, not wrapped. A wrap turns a loud moment into a full-scale
         * click, which is far more audible than the clipping would have been. */
        if (out >  32767.0f) out =  32767.0f;
        if (out < -32768.0f) out = -32768.0f;
        pcm[i] = (int16_t)lrintf(out);
        acc += fabsf(out);
    }
    st->level = (float)(acc / (double)samples / 32768.0);
}

float dsp_level(const dsp_state_t *st) { return st ? st->level : 0.0f; }

/* ── runtime tuning ─────────────────────────────────────── */

void dsp_set_gain_target_dbfs(float dbfs)
{
    if (dbfs > -6.0f)  dbfs = -6.0f;
    if (dbfs < -30.0f) dbfs = -30.0f;
    s_target = dbfs_to_lin(dbfs);
}

void dsp_set_gate_dbfs(float dbfs)
{
    if (dbfs <= -98.0f) { s_gate_thresh = 0.0f; return; }   /* off */
    if (dbfs > -20.0f)  dbfs = -20.0f;
    if (dbfs < -70.0f)  dbfs = -70.0f;
    s_gate_thresh = dbfs_to_lin(dbfs);
}

void  dsp_set_agc_enabled(bool on) { s_agc_on = on; }
float dsp_get_gain_target_dbfs(void)
{
    return 20.0f * log10f(s_target / 32768.0f);
}
float dsp_get_gate_dbfs(void)
{
    if (s_gate_thresh <= 0.0f) return -99.0f;
    return 20.0f * log10f(s_gate_thresh / 32768.0f);
}
bool  dsp_get_agc_enabled(void) { return s_agc_on; }
