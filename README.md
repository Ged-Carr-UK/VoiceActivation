# EAS Voice Controller — ESP32-S3 Firmware v1.2.0

Full-duplex voice controller for the EAS (Enterprise Archive Solution).
Captures mic audio via INMP441, plays TTS responses through a MAX98357A
amplifier and hobby speaker, and shows system state on a 4-LED WS2812B strip.

---

## Hardware Required

| Component | Part | Approx Cost |
|---|---|---|
| Microcontroller | ESP32-S3-DevKitC-1 | ~£10 |
| Microphone | INMP441 I2S MEMS Mic | ~£4 |
| Amplifier + DAC | MAX98357A breakout | ~£4 |
| Speaker | Hobby speaker 4Ω or 8Ω, 1–3W | ~£3 |
| LED strip | WS2812B × 4 LEDs (hobby kit) | ~£2 |
| Cable | USB-C | — |
| Optional | Push button (momentary) | ~£0.50 |
| **Total** | | **~£24** |

---

## Wiring

### INMP441 Microphone → ESP32-S3 (I2S Bus 1 — input)

```
INMP441          ESP32-S3 GPIO
───────          ─────────────
VDD      →       3.3V
GND      →       GND
L/R      →       GND       (ties to left channel — important)
SD       →       GPIO 2    ← I2S data in
SCK      →       GPIO 4    ← I2S bit clock
WS       →       GPIO 5    ← I2S word select
```

### MAX98357A Amplifier → ESP32-S3 (I2S Bus 0 — output)

```
MAX98357A        ESP32-S3 GPIO
─────────        ─────────────
VIN      →       5V
GND      →       GND
BCLK     →       GPIO 12   ← I2S bit clock out
LRC      →       GPIO 13   ← I2S word select out
DIN      →       GPIO 14   ← I2S data out
GAIN     →       Leave floating  (= 9dB default)
SD       →       Leave floating  (= always on)
```

### WS2812B LED Strip × 4 LEDs → ESP32-S3

```
LED Strip        ESP32-S3 GPIO
─────────        ─────────────
VCC / 5V →       5V (VIN pin)
GND      →       GND
DIN / DI →       GPIO 48   ← data in
DOUT/DO  →       Leave unconnected
```

> If your strip has a small resistor pad labelled R on the DIN line, that's
> normal — leave it. If yours doesn't, add a 300–500Ω resistor in series on
> the GPIO 48 → DIN wire to protect the first LED from ringing.

### Speaker → MAX98357A

```
Speaker (+) → MAX98357A (+) terminal
Speaker (-) → MAX98357A (–) terminal
```

Do NOT connect the speaker directly to ESP32 GPIO pins.

---

## LED Strip Behaviour

| Colour | Animation | State |
|---|---|---|
| 🔵 Blue | Slow chase — one LED cycles around | Idle, ready |
| 🔵 Blue | Gentle breathe — all 4 fade in/out | Listening (mic active) |
| 🟡 Amber | Knight Rider wipe back and forth | Processing (PC working) |
| 🟢 Green | Slow pulse — all 4 fade in/out | Speaking (TTS playing) |
| 🟢 Green | Fill left→right then clear | Success — command done |
| 🔴 Red | Three rapid flashes then clear | Error |

The built-in single LED on GPIO 38 mirrors the strip so you can see
status at any angle.

---

## Setup — PlatformIO (Recommended)

1. Install [VS Code](https://code.visualstudio.com/) + [PlatformIO extension](https://platformio.org/)
2. Open this folder in VS Code
3. PlatformIO auto-installs **Adafruit NeoPixel**
4. Click **Upload**
5. Open Serial Monitor at **921600 baud**
6. Expected output:
   ```
   EAS_STATUS:BOOT
   EAS_STATUS:VERSION_1.2.0
   EAS_STATUS:SAMPLE_RATE_16000
   EAS_STATUS:I2S_MIC_READY
   EAS_STATUS:I2S_SPK_READY
   EAS_STATUS:READY
   ```
   LED strip: blue chase should start immediately.

## Setup — Arduino IDE

1. Arduino IDE 2.x + ESP32 board support
2. Board: **ESP32S3 Dev Module**
3. **USB Mode: Hardware CDC and OTG** ← required
4. **CDC On Boot: Enabled** ← required
5. Library Manager: install **Adafruit NeoPixel**
6. Copy `src/main.cpp` into sketch, upload

---

## Configuration (include/config.h)

### LED strip
```cpp
#define PIN_STRIP_DATA    48     // Change if you use a different GPIO
#define STRIP_NUM_LEDS    4      // Change if your strip has more LEDs
#define STRIP_BRIGHTNESS  120    // 0–255 (lower = less power draw)

#define ENABLE_BUILTIN_LED  true  // Set false to disable the on-board LED
#define BUILTIN_BRIGHTNESS  60
```

### Mic + Speaker
```cpp
#define MIC_GAIN      2.0f   // Increase if mic too quiet (try 3.0)
#define SPK_VOLUME    0.80f  // Reduce if speaker distorts (try 0.6)
```

### GPIO pins
```cpp
// Microphone
#define PIN_MIC_SD    2
#define PIN_MIC_SCK   4
#define PIN_MIC_WS    5

// Speaker
#define PIN_SPK_BCLK  12
#define PIN_SPK_LRC   13
#define PIN_SPK_DIN   14
```

---

## Serial Protocol (unchanged from v1.1.0)

### ESP32 → PC
```
EAS_STATUS:READY             — Booted and ready
EAS_STATUS:STREAMING_START   — Mic stream started
EAS_STATUS:STREAMING_STOP    — Mic stream stopped
EAS_STATUS:TTS_PLAYING       — Speaker playback started
EAS_STATUS:TTS_DONE          — Speaker playback finished
EAS_STATUS:PONG              — Response to CMD:PING
```

### PC → ESP32 (commands)
```
CMD:PING / CMD:START / CMD:STOP
CMD:PROCESSING / CMD:SUCCESS / CMD:ERROR
CMD:SPK_VOLUME:75 / CMD:SPK_MUTE / CMD:SPK_UNMUTE
```

### PC → ESP32 (TTS audio)
```
[0xEA 0x54 0x54 0x53] + [N bytes 16kHz 16-bit PCM] + [0xEA 0x54 0x45 0x4E]
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Strip stays off | Check 5V on VCC, GND connected, DIN on GPIO 48 |
| Wrong colours | Some hobby strips use RGB not GRB — change `NEO_GRB` to `NEO_RGB` in `led_status.h` |
| Flickering strip | Add 300–500Ω resistor on DIN wire; add 100µF cap across 5V/GND |
| Only first LED works | Strip may need more current — try powering VCC from USB 5V directly, not through ESP32 |
| Mic too quiet | Increase `MIC_GAIN` to 3.0 or 4.0 |
| Speaker distorts | Reduce `SPK_VOLUME` to 0.6 |
| Device not found (Windows) | Install ESP32-S3 USB driver from Espressif docs |

---

## Next Steps

- **Phase 2** — PC Python daemon: Whisper STT + Edge-TTS response
- **Phase 3** — Intent parser: speech → EAS API commands
- **Phase 4** — React/Next.js WebSocket integration in EAS app
