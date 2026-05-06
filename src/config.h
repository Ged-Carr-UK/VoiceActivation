/**
 * config.h — EAS Voice Controller Configuration
 * ===============================================
 * Central configuration file. Adjust these values to match
 * your specific hardware wiring and requirements.
 *
 * Hardware in use:
 *   - AZ-Delivery ESP32 (38-pin dev board)
 *   - INMP441 I2S MEMS Microphone  (I2S bus 1 — RX)
 *   - MAX98357A I2S DAC Amplifier  (I2S bus 0 — TX)
 *   - Hobby speaker 4Ω or 8Ω
 *   - Optional WS2812B status LEDs (pin must be updated per board wiring)
 */

#pragma once

// ─── Firmware Version ─────────────────────────────────────────────────────────
#define FIRMWARE_VERSION "1.2.0"

// ─── MICROPHONE PINS — INMP441 → ESP32 (I2S Bus 1, RX) ──────────────────────
//
//   INMP441 pin    →    ESP32 GPIO
//   ───────────────────────────────────
//   VDD            →    3.3V
//   GND            →    GND
//   L/R            →    GND       (selects left channel)
//   SD             →    GPIO 2    ← I2S data in
//   SCK            →    GPIO 4    ← I2S bit clock
//   WS             →    GPIO 5    ← I2S word select
//
#define PIN_MIC_SD    2
#define PIN_MIC_SCK   4
#define PIN_MIC_WS    5

// ─── SPEAKER PINS — MAX98357A → ESP32 (I2S Bus 0, TX) ───────────────────────
//
//   MAX98357A pin  →    ESP32 GPIO
//   ───────────────────────────────────
//   VIN            →    5V
//   GND            →    GND
//   BCLK           →    GPIO 13
//   LRC            →    GPIO 12
//   DIN            →    GPIO 14
//   GAIN           →    Leave floating (9dB default)
//   SD             →    Leave floating (always on)
//
#define PIN_SPK_BCLK  13
#define PIN_SPK_LRC   12
#define PIN_SPK_DIN   14
#define PIN_SPK_SD    -1   // Set to a GPIO to enable software mute

// ─── LED STRIP — WS2812B × 4 LEDs (optional) ─────────────────────────────────
// Update PIN_STRIP_DATA to the actual GPIO used on the AZ-Delivery board
// before re-enabling LED support.
#define PIN_STRIP_DATA    22
#define STRIP_NUM_LEDS    4      // Number of LEDs on the strip
#define STRIP_BRIGHTNESS  120    // 0–255 (120 = good indoor brightness)

// ─── BUILT-IN LED ─────────────────────────────────────────────────────────────
// The AZ-Delivery 38-pin ESP32 does not provide the S3's built-in WS2812 LED.
#define ENABLE_BUILTIN_LED  false
#define PIN_BUILTIN_LED     0
#define BUILTIN_BRIGHTNESS  60   // Dimmer than strip — it's just a status dot

// ─── Optional: Push-to-Talk Button ───────────────────────────────────────────
#define ENABLE_PUSH_TO_TALK  false
#define PIN_BUTTON           0   // GPIO 0 = BOOT button on many ESP32 dev boards

// ─── AUDIO — MICROPHONE ───────────────────────────────────────────────────────
#define SAMPLE_RATE          16000
#define I2S_BUFFER_SAMPLES   512
#define DMA_BUF_COUNT        8
#define DMA_BUF_LEN          512
#define MIC_GAIN             2.0f  // Increase if mic is quiet (try 3.0)

// ─── AUDIO — SPEAKER ──────────────────────────────────────────────────────────
#define SPK_DMA_BUF_COUNT    8
#define SPK_DMA_BUF_LEN      512
#define SPK_VOLUME           0.80f  // 0.0–1.0 (reduce if speaker distorts)

// ─── Serial / USB ─────────────────────────────────────────────────────────────
#define SERIAL_BAUD_RATE     921600

// ─── Packet Framing ───────────────────────────────────────────────────────────
static const uint8_t PACKET_HEADER[]  = { 0xEA, 0x50, 0x43, 0x4D };  // êPCM
static const uint8_t PACKET_FOOTER[]  = { 0xEA, 0x45, 0x4E, 0x44 };  // êEND
static const size_t  PACKET_HEADER_LEN = sizeof(PACKET_HEADER);
static const size_t  PACKET_FOOTER_LEN = sizeof(PACKET_FOOTER);

static const uint8_t TTS_HEADER[]     = { 0xEA, 0x54, 0x54, 0x53 };  // êTTS
static const uint8_t TTS_FOOTER[]     = { 0xEA, 0x54, 0x45, 0x4E };  // êTEN
static const size_t  TTS_HEADER_LEN   = sizeof(TTS_HEADER);
static const size_t  TTS_FOOTER_LEN   = sizeof(TTS_FOOTER);

// ─── Silence Detection (VAD) ──────────────────────────────────────────────────
#define ENABLE_VAD             true
#define VAD_SILENCE_THRESHOLD  150
#define VAD_SILENCE_TIMEOUT_MS 2000
