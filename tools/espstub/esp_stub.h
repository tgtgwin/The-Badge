/* Just enough ESP-IDF to let clang parse the files the simulator cannot build.
 *
 * 🚨 Why this exists. sim/build.py leaves out everything in ESP_ONLY — main.c,
 *    port_esp.c, display.c, adpcm.c, rec_store.c, rec_upload.c, usb_export.c,
 *    usb_msc.c — because those are the board's half of port.h. On a machine
 *    with ESP-IDF installed that costs nothing: idf.py build compiles them.
 *    Without ESP-IDF installed it means the most delicate file in the firmware
 *    is the one nothing ever parses, and a typo in it is found by flashing a
 *    badge rather than by running a command.
 *
 * This is a parser's view, not an implementation. Nothing here does anything;
 * the only promise is that the *declarations* match ESP-IDF, so a call with the
 * wrong arguments or a misspelled field is caught. It cannot catch a wrong
 * argument order between two identically typed parameters, and it obviously
 * cannot catch wrong behaviour — it is a spell-checker, not a run.
 *
 * Declarations are taken from ESP-IDF v5.5 headers. If one drifts, this file
 * is the thing to fix, and the fix is a one-line change here rather than a
 * rewrite of the firmware.
 */
#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>

/* ── esp_err.h ─────────────────────────────────────────────── */
typedef int esp_err_t;
#define ESP_OK          0
#define ESP_FAIL        (-1)
#define ESP_ERR_NO_MEM  0x101
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_INVALID_SIZE 0x104
#define ESP_ERR_NOT_FOUND 0x105

/* ── esp_log.h ─────────────────────────────────────────────── */
typedef enum { ESP_LOG_NONE, ESP_LOG_ERROR, ESP_LOG_WARN, ESP_LOG_INFO, ESP_LOG_DEBUG, ESP_LOG_VERBOSE } esp_log_level_t;
void esp_log_write(esp_log_level_t level, const char *tag, const char *format, ...);
#define ESP_LOGE(tag, fmt, ...) esp_log_write(ESP_LOG_ERROR,   tag, fmt, ##__VA_ARGS__)
#define ESP_LOGW(tag, fmt, ...) esp_log_write(ESP_LOG_WARN,    tag, fmt, ##__VA_ARGS__)
#define ESP_LOGI(tag, fmt, ...) esp_log_write(ESP_LOG_INFO,    tag, fmt, ##__VA_ARGS__)
#define ESP_LOGD(tag, fmt, ...) esp_log_write(ESP_LOG_DEBUG,   tag, fmt, ##__VA_ARGS__)

/* ── esp_heap_caps.h ───────────────────────────────────────── */
#define MALLOC_CAP_SPIRAM    (1 << 10)
#define MALLOC_CAP_INTERNAL  (1 << 11)
#define MALLOC_CAP_8BIT      (1 << 2)
#define MALLOC_CAP_DMA       (1 << 3)
void *heap_caps_malloc(size_t size, uint32_t caps);
void *heap_caps_calloc(size_t n, size_t size, uint32_t caps);
void  heap_caps_free(void *ptr);
size_t heap_caps_get_free_size(uint32_t caps);
size_t heap_caps_get_largest_free_block(uint32_t caps);

/* ── esp_timer.h ───────────────────────────────────────────── */
int64_t esp_timer_get_time(void);              /* microseconds since boot */

typedef struct esp_timer *esp_timer_handle_t;
typedef void (*esp_timer_cb_t)(void *arg);
typedef enum { ESP_TIMER_TASK, ESP_TIMER_ISR } esp_timer_dispatch_t;
typedef struct {
    esp_timer_cb_t callback;
    void *arg;
    esp_timer_dispatch_t dispatch_method;
    const char *name;
    bool skip_unhandled_events;
} esp_timer_create_args_t;
esp_err_t esp_timer_create(const esp_timer_create_args_t *args, esp_timer_handle_t *out);
esp_err_t esp_timer_start_once(esp_timer_handle_t t, uint64_t us);
esp_err_t esp_timer_start_periodic(esp_timer_handle_t t, uint64_t us);
esp_err_t esp_timer_stop(esp_timer_handle_t t);
esp_err_t esp_timer_delete(esp_timer_handle_t t);
bool      esp_timer_is_active(esp_timer_handle_t t);

/* ── esp_partition.h ───────────────────────────────────────── */
typedef struct {
    esp_err_t (*init)(void);
    esp_err_t (*deinit)(void);
    esp_err_t (*read)(void);
    esp_err_t (*write)(void);
    esp_err_t (*erase)(void);
    esp_err_t (*mmap)(void);
} esp_partition_impl;

#define ESP_PARTITION_TYPE_APP  0x00
#define ESP_PARTITION_TYPE_DATA 0x01
#define ESP_PARTITION_SUBTYPE_ANY 0xFF

typedef struct {
    uint32_t magic;
    uint32_t type;
    uint32_t subtype;
    uint32_t address;
    uint32_t size;
    char     label[17];
    bool     encrypted;
    bool     readonly;
} esp_partition_t;

const esp_partition_t *esp_partition_find_first(uint32_t type, uint32_t subtype, const char *label);
esp_err_t esp_partition_read(const esp_partition_t *p, size_t src_offset, void *dst, size_t size);
esp_err_t esp_partition_write(const esp_partition_t *p, size_t dst_offset, const void *src, size_t size);
esp_err_t esp_partition_erase_range(const esp_partition_t *p, size_t offset, size_t size);

/* ── esp_codec_dev.h ────────────────────────────────────────
 * Only what the microphone path touches. */
typedef struct esp_codec_dev *esp_codec_dev_handle_t;
typedef enum { ESP_CODEC_DEV_TYPE_NONE = 0, ESP_CODEC_DEV_TYPE_IN = 1, ESP_CODEC_DEV_TYPE_OUT = 2 } esp_codec_dev_type_t;

typedef struct {
    uint8_t  bits_per_sample;
    uint8_t  channel;
    uint32_t sample_rate;
    uint8_t  mclk_multiple;
} esp_codec_dev_sample_info_t;

esp_err_t esp_codec_dev_open(esp_codec_dev_handle_t dev, esp_codec_dev_sample_info_t *fs);
esp_err_t esp_codec_dev_close(esp_codec_dev_handle_t dev);
esp_err_t esp_codec_dev_read(esp_codec_dev_handle_t dev, void *data, int len);
esp_err_t esp_codec_dev_write(esp_codec_dev_handle_t dev, void *data, int len);
esp_err_t esp_codec_dev_set_in_gain(esp_codec_dev_handle_t dev, float db);
esp_err_t esp_codec_dev_set_out_vol(esp_codec_dev_handle_t dev, int volume);

/* ── bsp/esp32_s3_touch_amoled_1_75c.h ──────────────────────
 * The board package. Only the audio entry point is needed by the recording
 * files; the display and touch entry points are in port_esp.c and display.c,
 * which this harness does not yet cover. */
esp_codec_dev_handle_t bsp_audio_codec_microphone_init(void);
esp_codec_dev_handle_t bsp_audio_codec_speaker_init(void);
