/**
 * EAS Voice Controller — ESP32-S3 Firmware v1.2.0
 * ==================================================
 * Full-duplex audio: captures mic via INMP441 (I2S bus 1)
 * and plays TTS responses via MAX98357A speaker (I2S bus 0).
 * Both run simultaneously on separate I2S peripherals.
 * 4-LED WS2812B strip shows system state with colour + animation.
 *
 * Hardware:
 *   - ESP32-S3 DevKitC-1
 *   - INMP441 I2S MEMS Microphone
 *   - MAX98357A I2S DAC + Amplifier
 *   - Hobby speaker (4Ω or 8Ω, up to 3W)
 *   - WS2812B LED strip × 4 LEDs
 *   - Optional: Push button for push-to-talk
 *
 * Wiring summary (full detail in config.h):
 *   INMP441:   SD→GPIO2,  SCK→GPIO4,  WS→GPIO5,   L/R→GND, VDD→3.3V
 *   MAX98357A: BCLK→GPIO12, LRC→GPIO13, DIN→GPIO14, VIN→5V
 *   LED strip: DIN→GPIO48, VCC→5V, GND→GND
 *   Speaker: connect to MAX98357A + and – terminals directly
 */

#include <Arduino.h>
#include <driver/i2s.h>
#include "config.h"
#include "led_status.h"
#include "serial_protocol.h"

// ─── I2S Bus Assignments ──────────────────────────────────────────────────────
#define I2S_MIC_PORT   I2S_NUM_1   // Microphone — RX only
#define I2S_SPK_PORT   I2S_NUM_0   // Speaker    — TX only

// ─── State Machine ────────────────────────────────────────────────────────────
enum class DeviceState {
  IDLE,        // Waiting        — LED: slow blue pulse
  LISTENING,   // Streaming mic  — LED: solid blue
  PROCESSING,  // PC working     — LED: amber pulse
  SPEAKING,    // Playing TTS    — LED: slow green pulse
  SUCCESS,     // Command done   — LED: green flash
  ERROR        // Failed         — LED: red flash
};

static DeviceState currentState = DeviceState::IDLE;

// ─── Audio Buffers ────────────────────────────────────────────────────────────
// Mic: raw 32-bit from INMP441, converted to 16-bit for streaming
static int32_t  micRaw32[I2S_BUFFER_SAMPLES];
static int16_t  micPCM16[I2S_BUFFER_SAMPLES];

// Speaker: incoming TTS bytes from serial, written to I2S
// Ring buffer to decouple serial reads from I2S writes
static const size_t SPK_RING_SIZE = 8192;  // 8KB — ~250ms at 16kHz 16-bit
static uint8_t  spkRingBuf[SPK_RING_SIZE];
static volatile size_t spkRingHead = 0;
static volatile size_t spkRingTail = 0;
static volatile bool   spkPlaying  = false;
static float           spkVolume   = SPK_VOLUME;
static bool            spkI2SReady = false;

// Speaker volume (0.0–1.0), adjusted via CMD:SPK_VOLUME
static bool spkMuted = true;

// ─── TTS Packet State Machine ─────────────────────────────────────────────────
// Tracks incoming TTS audio packets from the PC on the serial line
enum class TTSRxState {
  WAITING_HEADER,   // scanning for 0xEA 0x54 0x54 0x53
  IN_PAYLOAD,       // accumulating audio bytes
  WAITING_FOOTER    // looking for 0xEA 0x54 0x45 0x4E
};

static TTSRxState ttsRxState   = TTSRxState::WAITING_HEADER;
static uint8_t    ttsHeaderIdx = 0;
static uint8_t    ttsFooterIdx = 0;

// ─── Ring Buffer Helpers ──────────────────────────────────────────────────────
inline size_t ringAvailable() {
  return (spkRingHead >= spkRingTail)
    ? (spkRingHead - spkRingTail)
    : (SPK_RING_SIZE - spkRingTail + spkRingHead);
}

inline bool ringWrite(uint8_t b) {
  size_t next = (spkRingHead + 1) % SPK_RING_SIZE;
  if (next == spkRingTail) return false;  // Full
  spkRingBuf[spkRingHead] = b;
  spkRingHead = next;
  return true;
}

inline bool ringRead(uint8_t &b) {
  if (spkRingHead == spkRingTail) return false;  // Empty
  b = spkRingBuf[spkRingTail];
  spkRingTail = (spkRingTail + 1) % SPK_RING_SIZE;
  return true;
}

// ─── I2S Microphone Init (Bus 1 — RX) ────────────────────────────────────────
void initMicI2S() {
  i2s_config_t cfg = {
    .mode                 = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate          = SAMPLE_RATE,
    .bits_per_sample      = I2S_BITS_PER_SAMPLE_32BIT,  // INMP441 outputs 32-bit
    .channel_format       = I2S_CHANNEL_FMT_ONLY_LEFT,  // L/R → GND = left ch
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count        = DMA_BUF_COUNT,
    .dma_buf_len          = DMA_BUF_LEN,
    .use_apll             = false,
    .tx_desc_auto_clear   = false,
    .fixed_mclk           = 0
  };

  i2s_pin_config_t pins = {
    .bck_io_num   = PIN_MIC_SCK,
    .ws_io_num    = PIN_MIC_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num  = PIN_MIC_SD
  };

  ESP_ERROR_CHECK(i2s_driver_install(I2S_MIC_PORT, &cfg, 0, NULL));
  ESP_ERROR_CHECK(i2s_set_pin(I2S_MIC_PORT, &pins));
  ESP_ERROR_CHECK(i2s_zero_dma_buffer(I2S_MIC_PORT));

  Serial.println(STATUS_I2S_MIC_READY);
}

// ─── I2S Speaker Init (Bus 0 — TX) ───────────────────────────────────────────
void initSpeakerI2S() {
  i2s_config_t cfg = {
    .mode                 = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate          = SAMPLE_RATE,        // Same rate as mic — 16kHz
    .bits_per_sample      = I2S_BITS_PER_SAMPLE_16BIT,  // MAX98357A handles 16-bit
    .channel_format       = I2S_CHANNEL_FMT_ONLY_LEFT,  // Mono output
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count        = SPK_DMA_BUF_COUNT,
    .dma_buf_len          = SPK_DMA_BUF_LEN,
    .use_apll             = false,
    .tx_desc_auto_clear   = true,   // Fill underruns with silence automatically
    .fixed_mclk           = 0
  };

  i2s_pin_config_t pins = {
    .bck_io_num   = PIN_SPK_BCLK,
    .ws_io_num    = PIN_SPK_LRC,
    .data_out_num = PIN_SPK_DIN,
    .data_in_num  = I2S_PIN_NO_CHANGE
  };

  ESP_ERROR_CHECK(i2s_driver_install(I2S_SPK_PORT, &cfg, 0, NULL));
  ESP_ERROR_CHECK(i2s_set_pin(I2S_SPK_PORT, &pins));
  ESP_ERROR_CHECK(i2s_zero_dma_buffer(I2S_SPK_PORT));

  // Software mute pin (optional)
#if PIN_SPK_SD >= 0
  pinMode(PIN_SPK_SD, OUTPUT);
  digitalWrite(PIN_SPK_SD, HIGH);  // HIGH = enabled
#endif

  spkI2SReady = true;
  Serial.println(STATUS_I2S_SPK_READY);
}

void ensureSpeakerI2S() {
  if (!spkI2SReady) {
    initSpeakerI2S();
  }
}

// ─── Read Mic and Convert to 16-bit ──────────────────────────────────────────
bool readMicSamples(size_t &samplesRead) {
  size_t bytesRead = 0;
  esp_err_t result = i2s_read(
    I2S_MIC_PORT,
    micRaw32,
    sizeof(micRaw32),
    &bytesRead,
    pdMS_TO_TICKS(50)
  );

  if (result != ESP_OK || bytesRead == 0) return false;

  samplesRead = bytesRead / sizeof(int32_t);
  for (size_t i = 0; i < samplesRead; i++) {
    // INMP441: audio is in upper 18 bits of 32-bit word
    // Shift right 14 to get signed 18-bit, then scale with gain
    float sample = (float)(micRaw32[i] >> 14) * MIC_GAIN;
    // Clamp to int16 range
    if      (sample >  32767.0f) sample =  32767.0f;
    else if (sample < -32768.0f) sample = -32768.0f;
    micPCM16[i] = (int16_t)sample;
  }

  return true;
}

// ─── Stream Mic Packet to PC ──────────────────────────────────────────────────
void streamMicPacket(size_t samples) {
  size_t byteCount = samples * sizeof(int16_t);
  Serial.write(PACKET_HEADER, PACKET_HEADER_LEN);
  Serial.write((uint8_t*)micPCM16, byteCount);
  Serial.write(PACKET_FOOTER, PACKET_FOOTER_LEN);
}

// ─── Voice Activity Detection ─────────────────────────────────────────────────
#if ENABLE_VAD
static uint32_t lastSoundTime = 0;

bool isSilent(size_t samples) {
  int64_t sum = 0;
  for (size_t i = 0; i < samples; i++) sum += abs(micPCM16[i]);
  int16_t rms = (int16_t)(sum / samples);

  if (rms > VAD_SILENCE_THRESHOLD) {
    lastSoundTime = millis();
    return false;
  }
  return (millis() - lastSoundTime) > VAD_SILENCE_TIMEOUT_MS;
}
#endif

// ─── Speaker Playback Task ────────────────────────────────────────────────────
// Drains the ring buffer into I2S whenever data is available.
// Call every loop() — writes up to one DMA buffer worth at a time.
void drainSpeakerBuffer() {
  if (spkRingHead == spkRingTail) {
    // Ring buffer empty — if we were playing, we're done
    if (spkPlaying) {
      spkPlaying = false;
      // Flush I2S TX to push any partial DMA buffer
      if (spkI2SReady) {
        i2s_zero_dma_buffer(I2S_SPK_PORT);
      }
      currentState = DeviceState::IDLE;
      ledSetState(LEDState::IDLE);
      Serial.println(STATUS_TTS_DONE);
    }
    return;
  }

  // Prepare a local write buffer (one DMA buffer worth = 512 samples = 1024 bytes)
  static int16_t spkWriteBuf[SPK_DMA_BUF_LEN];
  size_t filled = 0;

  while (filled < SPK_DMA_BUF_LEN) {
    uint8_t lo, hi;
    if (!ringRead(lo)) break;
    if (!ringRead(hi)) { ringWrite(lo); break; }  // Odd byte — put back

    int16_t sample = (int16_t)(lo | (hi << 8));

    // Apply volume and mute
    if (spkMuted) {
      sample = 0;
    } else {
      sample = (int16_t)((float)sample * spkVolume);
    }

    spkWriteBuf[filled++] = sample;
  }

  if (filled == 0) return;

  ensureSpeakerI2S();

  size_t bytesWritten = 0;
  i2s_write(
    I2S_SPK_PORT,
    spkWriteBuf,
    filled * sizeof(int16_t),
    &bytesWritten,
    pdMS_TO_TICKS(20)
  );
}

// ─── Parse Incoming TTS Audio Bytes ──────────────────────────────────────────
// Called byte-by-byte from handleSerialInput() for non-command bytes.
// Implements a simple state machine to find TTS packet boundaries.
void processTTSByte(uint8_t b) {
  switch (ttsRxState) {

    case TTSRxState::WAITING_HEADER:
      // Look for TTS_HEADER sequence byte by byte
      if (b == TTS_HEADER[ttsHeaderIdx]) {
        ttsHeaderIdx++;
        if (ttsHeaderIdx == TTS_HEADER_LEN) {
          ttsHeaderIdx = 0;
          ttsRxState   = TTSRxState::IN_PAYLOAD;
          if (!spkPlaying) {
            spkPlaying   = true;
            currentState = DeviceState::SPEAKING;
            ledSetState(LEDState::SPEAKING);
            Serial.println(STATUS_TTS_PLAYING);
          }
        }
      } else {
        ttsHeaderIdx = 0;
      }
      break;

    case TTSRxState::IN_PAYLOAD:
      // Check if this byte starts the footer
      if (b == TTS_FOOTER[ttsFooterIdx]) {
        ttsFooterIdx++;
        if (ttsFooterIdx == TTS_FOOTER_LEN) {
          // Complete footer found — packet done
          ttsFooterIdx = 0;
          ttsRxState   = TTSRxState::WAITING_HEADER;
        }
        // Don't write footer bytes to the ring buffer
      } else {
        // Not a footer byte — flush any partial footer match into ring buffer
        for (uint8_t i = 0; i < ttsFooterIdx; i++) ringWrite(TTS_FOOTER[i]);
        ttsFooterIdx = 0;
        ringWrite(b);
      }
      break;

    case TTSRxState::WAITING_FOOTER:
      // Unused — handled inline in IN_PAYLOAD
      break;
  }
}

// ─── Handle Incoming Serial Data ──────────────────────────────────────────────
// Reads all available serial bytes. ASCII commands are handled directly.
// Binary TTS audio bytes are routed through the TTS state machine.
void handleSerialInput() {
  while (Serial.available()) {
    // Peek at the next byte
    int peeked = Serial.peek();
    if (peeked < 0) break;

    // If it looks like the start of a text command, read a full line
    if (peeked == 'C') {  // All commands start with 'C' (CMD:...)
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();

      if (cmd == CMD_PING) {
        Serial.println(STATUS_PONG);

      } else if (cmd == CMD_START) {
        currentState = DeviceState::LISTENING;
        ledSetState(LEDState::LISTENING);
        Serial.println(STATUS_STREAM_START);

      } else if (cmd == CMD_STOP) {
        currentState = DeviceState::IDLE;
        ledSetState(LEDState::IDLE);
        Serial.println(STATUS_STREAM_STOP);

      } else if (cmd == CMD_PROCESSING) {
        currentState = DeviceState::PROCESSING;
        ledSetState(LEDState::PROCESSING);

      } else if (cmd == CMD_SUCCESS) {
        currentState = DeviceState::SUCCESS;
        ledSetState(LEDState::SUCCESS);
        delay(800);
        if (currentState == DeviceState::SUCCESS) {
          currentState = DeviceState::IDLE;
          ledSetState(LEDState::IDLE);
        }

      } else if (cmd == CMD_ERROR) {
        currentState = DeviceState::ERROR;
        ledSetState(LEDState::ERROR);
        delay(800);
        if (currentState == DeviceState::ERROR) {
          currentState = DeviceState::IDLE;
          ledSetState(LEDState::IDLE);
        }

      } else if (cmd == CMD_SPK_TEST) {
        ensureSpeakerI2S();
        // Generate 440Hz tone directly via I2S — no serial audio path
        Serial.println("EAS_STATUS:SPK_TEST_START");
        static int16_t testBuf[512];
        for (int rep = 0; rep < 80; rep++) {  // ~2 seconds
          for (int i = 0; i < 512; i++) {
            float t = (float)((rep * 512 + i) % (SAMPLE_RATE / 440)) / (SAMPLE_RATE / 440);
            testBuf[i] = (int16_t)(sinf(2.0f * M_PI * t) * 28000);
          }
          size_t written = 0;
          i2s_write(I2S_SPK_PORT, testBuf, sizeof(testBuf), &written, pdMS_TO_TICKS(100));
        }
        Serial.println("EAS_STATUS:SPK_TEST_DONE");

      } else if (cmd == CMD_LED_OFF) {
        ledSetState(LEDState::OFF);

      } else if (cmd == CMD_SPK_MUTE) {
        spkMuted = true;

      } else if (cmd == CMD_SPK_UNMUTE) {
        spkMuted = false;

      } else if (cmd.startsWith("CMD:SPK_VOLUME:")) {
        // e.g. "CMD:SPK_VOLUME:75"
        int vol = cmd.substring(15).toInt();
        vol = constrain(vol, 0, 100);
        spkVolume = (float)vol / 100.0f;
        Serial.printf("EAS_STATUS:SPK_VOLUME_%d\n", vol);
      }

    } else {
      // Not a text command — route byte through TTS audio state machine
      uint8_t b = (uint8_t)Serial.read();
      processTTSByte(b);
    }
  }
}

// ─── Push-to-Talk Button ──────────────────────────────────────────────────────
#if ENABLE_PUSH_TO_TALK
static bool lastBtnState = HIGH;

void handleButton() {
  bool btnState = digitalRead(PIN_BUTTON);

  if (btnState == LOW && lastBtnState == HIGH) {
    if (currentState == DeviceState::IDLE) {
      currentState = DeviceState::LISTENING;
      ledSetState(LEDState::LISTENING);
      Serial.println(STATUS_BTN_START);
    }
  }

  if (btnState == HIGH && lastBtnState == LOW) {
    if (currentState == DeviceState::LISTENING) {
      currentState = DeviceState::PROCESSING;
      ledSetState(LEDState::PROCESSING);
      Serial.println(STATUS_BTN_STOP);
    }
  }

  lastBtnState = btnState;
}
#endif

// ─── Setup ────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(SERIAL_BAUD_RATE);
  // Avoid blocking forever when connected via UART bridge instead of native USB CDC.
  uint32_t serialWaitStart = millis();
  while (!Serial && (millis() - serialWaitStart) < 2000) {
    delay(10);
  }

  Serial.println(STATUS_BOOT);
  Serial.printf("EAS_STATUS:VERSION_%s\n", FIRMWARE_VERSION);
  Serial.printf("EAS_STATUS:SAMPLE_RATE_%d\n", SAMPLE_RATE);

  ledInit();
  ledSetState(LEDState::OFF);

#if ENABLE_PUSH_TO_TALK
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  Serial.println("EAS_STATUS:PTT_ENABLED");
#endif

  // Keep startup quiet: initialize mic immediately but defer speaker I2S
  // until first playback/test command.
  initMicI2S();

  Serial.println(STATUS_READY);
}

// ─── Main Loop ────────────────────────────────────────────────────────────────
void loop() {
  // 1. Handle all incoming serial (commands + TTS audio bytes)
  handleSerialInput();

#if ENABLE_PUSH_TO_TALK
  handleButton();
#endif

  // 2. Update LED animations
  ledUpdate();

  // 3. Drain TTS ring buffer to speaker I2S (always — runs in background)
  drainSpeakerBuffer();

  // 4. Stream mic to PC when listening
  if (currentState == DeviceState::LISTENING) {
    size_t samplesRead = 0;
    if (readMicSamples(samplesRead)) {

#if ENABLE_VAD
      if (isSilent(samplesRead)) {
        // Silence timeout reached — tell PC to stop
        currentState = DeviceState::IDLE;
        ledSetState(LEDState::IDLE);
        Serial.println(STATUS_STREAM_STOP);
        Serial.println(STATUS_VAD_SILENCE);
        return;
      }
#endif

      streamMicPacket(samplesRead);
    }
  }
}
