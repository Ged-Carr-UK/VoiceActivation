/**
 * serial_protocol.h — Serial Command & Status String Definitions
 * ===============================================================
 * All CMD_* strings are sent FROM the PC TO the ESP32.
 * All STATUS_* strings are sent FROM the ESP32 TO the PC.
 */

#pragma once

// ─── Commands (PC → ESP32) ────────────────────────────────────────────────────
#define CMD_PING          "CMD:PING"
#define CMD_START         "CMD:START"
#define CMD_STOP          "CMD:STOP"
#define CMD_PROCESSING    "CMD:PROCESSING"
#define CMD_SUCCESS       "CMD:SUCCESS"
#define CMD_ERROR         "CMD:ERROR"
#define CMD_SPK_MUTE      "CMD:SPK_MUTE"
#define CMD_SPK_UNMUTE    "CMD:SPK_UNMUTE"
#define CMD_LED_OFF       "CMD:LED_OFF"
#define CMD_SPK_TEST      "CMD:SPK_TEST"
// Dynamic command: "CMD:SPK_VOLUME:<0-100>" — parsed inline in handleSerialInput()

// ─── Status messages (ESP32 → PC) ────────────────────────────────────────────
#define STATUS_BOOT           "EAS_STATUS:BOOT"
#define STATUS_READY          "EAS_STATUS:READY"
#define STATUS_PONG           "EAS_STATUS:PONG"
#define STATUS_I2S_MIC_READY  "EAS_STATUS:I2S_MIC_READY"
#define STATUS_I2S_SPK_READY  "EAS_STATUS:I2S_SPK_READY"
#define STATUS_STREAM_START   "EAS_STATUS:STREAM_START"
#define STATUS_STREAM_STOP    "EAS_STATUS:STREAM_STOP"
#define STATUS_TTS_PLAYING    "EAS_STATUS:TTS_PLAYING"
#define STATUS_TTS_DONE       "EAS_STATUS:TTS_DONE"
#define STATUS_VAD_SILENCE    "EAS_STATUS:VAD_SILENCE"
#define STATUS_BTN_START      "EAS_STATUS:BTN_START"
#define STATUS_BTN_STOP       "EAS_STATUS:BTN_STOP"
