/* The FreeRTOS surface the recording files use. See esp_stub.h for why. */
#pragma once
#include "esp_stub.h"

typedef void *TaskHandle_t;
typedef void *SemaphoreHandle_t;
typedef void *QueueHandle_t;
typedef uint32_t TickType_t;
typedef int      BaseType_t;

#define pdTRUE   1
#define pdFALSE  0
#define pdPASS   1
#define pdFAIL   0
#define portMAX_DELAY 0xFFFFFFFFu
#define configTICK_RATE_HZ 1000

#define pdMS_TO_TICKS(ms) ((TickType_t)(ms))
#define pdTICKS_TO_MS(t)  ((uint32_t)(t))

BaseType_t xTaskCreate(void (*fn)(void *), const char *name, uint32_t stack,
                       void *arg, unsigned prio, TaskHandle_t *out);
BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *name, uint32_t stack,
                                   void *arg, unsigned prio, TaskHandle_t *out, int core);
void       vTaskDelete(TaskHandle_t t);
void       vTaskDelay(TickType_t ticks);
TickType_t xTaskGetTickCount(void);
void       vTaskSuspend(TaskHandle_t t);
void       vTaskResume(TaskHandle_t t);
void       vTaskPrioritySet(TaskHandle_t t, unsigned prio);
TaskHandle_t xTaskGetCurrentTaskHandle(void);

SemaphoreHandle_t xSemaphoreCreateMutex(void);
SemaphoreHandle_t xSemaphoreCreateBinary(void);
BaseType_t xSemaphoreTake(SemaphoreHandle_t s, TickType_t wait);
BaseType_t xSemaphoreGive(SemaphoreHandle_t s);

/* The critical-section macros. portMUX_TYPE is a struct on the real thing. */
typedef struct { int owner; } portMUX_TYPE;
#define portMUX_INITIALIZER_UNLOCKED { 0 }
void portENTER_CRITICAL(portMUX_TYPE *m);
void portEXIT_CRITICAL(portMUX_TYPE *m);
