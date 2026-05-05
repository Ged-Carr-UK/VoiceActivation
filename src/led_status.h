/**
 * led_status.h — LED Strip & Built-in LED Visual Feedback
 * =========================================================
 * Controls a 4-LED WS2812B hobby strip on GPIO 48 plus the
 * built-in single LED on GPIO 38. Both always show the same
 * state so you can see device status from across the room.
 *
 * Colour & animation guide
 * ─────────────────────────
 *   IDLE       Blue    Slow chase — LEDs light one at a time, cycling
 *   LISTENING  Blue    All LEDs solid, breathing gently in/out
 *   PROCESSING Amber   Fast back-and-forth wipe (Knight Rider style)
 *   SPEAKING   Green   Slow pulse — all LEDs fade in and out together
 *   SUCCESS    Green   Quick fill left→right, then all off
 *   ERROR      Red     Rapid all-on / all-off flash × 3
 *
 * Why separate animations per state?
 *   Colour alone can be ambiguous in a bright room. The animation
 *   pattern gives a second visual cue so you always know the state
 *   even at a glance.
 */

#pragma once
#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include "config.h"

// ─── Pixel objects ────────────────────────────────────────────────────────────
// Strip: 4 LEDs on GPIO 48
static Adafruit_NeoPixel strip(STRIP_NUM_LEDS, PIN_STRIP_DATA, NEO_GRB + NEO_KHZ800);

// Built-in: 1 LED on GPIO 38 (mirrors strip colour)
#if ENABLE_BUILTIN_LED
static Adafruit_NeoPixel builtin(1, PIN_BUILTIN_LED, NEO_GRB + NEO_KHZ800);
#endif

// ─── State enum ───────────────────────────────────────────────────────────────
enum class LEDState {
  OFF,
  IDLE,
  LISTENING,
  PROCESSING,
  SPEAKING,
  SUCCESS,
  ERROR
};

// ─── Internal animation state ─────────────────────────────────────────────────
static LEDState  _state      = LEDState::IDLE;
static uint32_t  _lastTick   = 0;   // millis() of last animation step
static uint8_t   _step       = 0;   // generic animation counter
static int8_t    _dir        = 1;   // direction for wipes/sweeps
static uint8_t   _brightness = 0;   // current brightness for breathe effects
static int8_t    _bDir       = 1;   // breathe direction (+1 rising, -1 falling)
static uint8_t   _flashCount = 0;   // flash counter for SUCCESS / ERROR

// ─── Helpers ──────────────────────────────────────────────────────────────────

// Set every LED on the strip to one colour + brightness, and mirror to builtin
static void setAll(uint8_t r, uint8_t g, uint8_t b) {
  for (int i = 0; i < STRIP_NUM_LEDS; i++) strip.setPixelColor(i, r, g, b);
  strip.show();
#if ENABLE_BUILTIN_LED
  builtin.setPixelColor(0, r, g, b);
  builtin.show();
#endif
}

// Clear all LEDs
static void clearAll() { setAll(0, 0, 0); }

// Set a single strip pixel (builtin mirrors the first pixel's colour)
static void setPixel(uint8_t idx, uint8_t r, uint8_t g, uint8_t b) {
  strip.setPixelColor(idx, r, g, b);
  strip.show();
#if ENABLE_BUILTIN_LED
  if (idx == 0) {
    builtin.setPixelColor(0, r, g, b);
    builtin.show();
  }
#endif
}

// ─── Public API ───────────────────────────────────────────────────────────────

void ledInit() {
  strip.begin();
  strip.setBrightness(STRIP_BRIGHTNESS);
  strip.clear();
  strip.show();

#if ENABLE_BUILTIN_LED
  builtin.begin();
  builtin.setBrightness(BUILTIN_BRIGHTNESS);
  builtin.clear();
  builtin.show();
#endif
}

// Call when changing state — resets animation counters and shows initial frame
void ledSetState(LEDState newState) {
  _state      = newState;
  _lastTick   = millis();
  _step       = 0;
  _dir        = 1;
  _brightness = 0;
  _bDir       = 1;
  _flashCount = 0;

  clearAll();

  // Snap to initial frame so there's no blank moment at transition
  switch (newState) {
    case LEDState::LISTENING:
      // Start with all blue at half brightness — breathe will animate from here
      setAll(0, 0, 80);
      _brightness = 80;
      break;

    case LEDState::SPEAKING:
      // Start with a dim green
      setAll(0, 40, 0);
      _brightness = 40;
      break;

    case LEDState::SUCCESS:
      // Kick off the left→right fill immediately
      setPixel(0, 0, 220, 0);
      _step = 1;
      break;

    case LEDState::ERROR:
      // Flash all red immediately
      setAll(220, 0, 0);
      _flashCount = 1;
      break;

    default:
      break;  // IDLE and PROCESSING animate fully in ledUpdate()

    case LEDState::OFF:
      clearAll();
      strip.show();
#if ENABLE_BUILTIN_LED
      builtin.setPixelColor(0, 0, 0, 0);
      builtin.show();
#endif
      break;
  }
}

// Call every loop() — drives all animations without blocking
void ledUpdate() {
  uint32_t now = millis();

  switch (_state) {

    case LEDState::OFF:
      break;  // Nothing to animate

    // ── IDLE: slow blue chase ──────────────────────────────────────────────
    // One LED lights at a time, cycling 0→1→2→3→0, period ~1.2 s
    case LEDState::IDLE: {
      if (now - _lastTick < 300) break;
      _lastTick = now;

      clearAll();
      // Primary lit LED
      strip.setPixelColor(_step % STRIP_NUM_LEDS, 0, 0, 100);
      // Dim trail on the previous LED for a smooth sweep feel
      uint8_t trail = (_step + STRIP_NUM_LEDS - 1) % STRIP_NUM_LEDS;
      strip.setPixelColor(trail, 0, 0, 25);
      strip.show();

#if ENABLE_BUILTIN_LED
      // Builtin mirrors the brightness of the lit LED
      builtin.setPixelColor(0, 0, 0, (_step % STRIP_NUM_LEDS == 0) ? 100 : 25);
      builtin.show();
#endif

      _step = (_step + 1) % STRIP_NUM_LEDS;
      break;
    }

    // ── LISTENING: all blue, gentle breathe ───────────────────────────────
    // All 4 LEDs pulse together — you're being heard
    case LEDState::LISTENING: {
      if (now - _lastTick < 12) break;
      _lastTick = now;

      _brightness += _bDir * 2;
      if (_brightness >= 180) { _brightness = 180; _bDir = -1; }
      if (_brightness <=  20) { _brightness =  20; _bDir =  1; }

      setAll(0, 0, _brightness);
      break;
    }

    // ── PROCESSING: amber Knight Rider wipe ───────────────────────────────
    // Bright dot bounces left and right — active, working
    case LEDState::PROCESSING: {
      if (now - _lastTick < 80) break;
      _lastTick = now;

      clearAll();
      // Bright leading dot
      strip.setPixelColor(_step, 220, 80, 0);
      // Dim neighbour for motion blur
      int neighbour = (int)_step - _dir;
      if (neighbour >= 0 && neighbour < STRIP_NUM_LEDS)
        strip.setPixelColor(neighbour, 60, 20, 0);
      strip.show();

#if ENABLE_BUILTIN_LED
      builtin.setPixelColor(0, 220, 80, 0);
      builtin.show();
#endif

      _step += _dir;
      if (_step >= STRIP_NUM_LEDS - 1) { _step = STRIP_NUM_LEDS - 1; _dir = -1; }
      if (_step <= 0)                  { _step = 0;                   _dir =  1; }
      break;
    }

    // ── SPEAKING: all green, slow pulse ───────────────────────────────────
    // Gentler than LISTENING breathe — calm, the system is responding
    case LEDState::SPEAKING: {
      if (now - _lastTick < 18) break;
      _lastTick = now;

      _brightness += _bDir * 2;
      if (_brightness >= 160) { _brightness = 160; _bDir = -1; }
      if (_brightness <=  10) { _brightness =  10; _bDir =  1; }

      setAll(0, _brightness, _brightness / 8);  // Slight teal tint
      break;
    }

    // ── SUCCESS: green fill left→right then clear ─────────────────────────
    // Satisfying "complete" wipe — one LED per tick
    case LEDState::SUCCESS: {
      if (now - _lastTick < 120) break;
      _lastTick = now;

      if (_step < STRIP_NUM_LEDS) {
        // Fill next LED
        strip.setPixelColor(_step, 0, 220, 0);
        strip.show();
#if ENABLE_BUILTIN_LED
        builtin.setPixelColor(0, 0, 220, 0);
        builtin.show();
#endif
        _step++;
      } else {
        // All lit — hold briefly then clear and return to IDLE
        delay(300);
        clearAll();
        ledSetState(LEDState::IDLE);
      }
      break;
    }

    // ── ERROR: red flash × 3 then clear ───────────────────────────────────
    // Unmistakable — something went wrong
    case LEDState::ERROR: {
      if (now - _lastTick < 150) break;
      _lastTick   = now;
      _flashCount++;

      bool on = (_flashCount % 2 == 1);
      setAll(on ? 220 : 0, 0, 0);

      if (_flashCount >= 6) {  // 3 flashes (on+off = 2 counts each)
        clearAll();
        ledSetState(LEDState::IDLE);
      }
      break;
    }
  }
}
