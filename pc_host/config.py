"""
config.py — EAS Voice Controller PC Host Configuration
=======================================================
All settings are read from environment variables (via .env file).
Packet framing constants MUST match config.h on the ESP32 firmware.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ─── Serial / USB ─────────────────────────────────────────────────────────────
SERIAL_PORT = os.getenv("SERIAL_PORT", "COM3")   # Change to match your ESP32 port
SERIAL_BAUD = 921600

# ─── Audio ────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000   # Hz — must match firmware SAMPLE_RATE
CHANNELS    = 1       # Mono
# Optional input device selector for sounddevice:
# - Empty: use OS default, then fallback to first working input device
# - Integer string (e.g. "2"): use device index
# - Text (e.g. "USB Microphone"): first input device containing this name
AUDIO_INPUT_DEVICE = os.getenv("AUDIO_INPUT_DEVICE", "").strip()

# ─── Packet framing — MUST match config.h on ESP32 ───────────────────────────
PCM_HEADER = bytes([0xEA, 0x50, 0x43, 0x4D])   # êPCM  — mic audio from ESP32
PCM_FOOTER = bytes([0xEA, 0x45, 0x4E, 0x44])   # êEND
TTS_HEADER = bytes([0xEA, 0x54, 0x54, 0x53])   # êTTS  — TTS audio to ESP32
TTS_FOOTER = bytes([0xEA, 0x54, 0x45, 0x4E])   # êTEN

# ─── Commands (PC → ESP32) ────────────────────────────────────────────────────
CMD_PING        = "CMD:PING"
CMD_START       = "CMD:START"
CMD_STOP        = "CMD:STOP"
CMD_PROCESSING  = "CMD:PROCESSING"
CMD_SUCCESS     = "CMD:SUCCESS"
CMD_ERROR       = "CMD:ERROR"
CMD_SPK_MUTE    = "CMD:SPK_MUTE"
CMD_SPK_UNMUTE  = "CMD:SPK_UNMUTE"

# ─── Status tokens (ESP32 → PC) ───────────────────────────────────────────────
STATUS_READY        = "EAS_STATUS:READY"
STATUS_STREAM_START = "EAS_STATUS:STREAM_START"
STATUS_STREAM_STOP  = "EAS_STATUS:STREAM_STOP"
STATUS_VAD_SILENCE  = "EAS_STATUS:VAD_SILENCE"
STATUS_TTS_PLAYING  = "EAS_STATUS:TTS_PLAYING"
STATUS_TTS_DONE     = "EAS_STATUS:TTS_DONE"
STATUS_PONG         = "EAS_STATUS:PONG"

# ─── Wake Word ────────────────────────────────────────────────────────────────
# Built-in openwakeword models: hey_jarvis, alexa, hey_mycroft, timer, weather
WAKE_WORD_MODEL     = os.getenv("WAKE_WORD_MODEL", "hey_jarvis")
WAKE_WORD_THRESHOLD = float(os.getenv("WAKE_WORD_THRESHOLD", "0.5"))
WAKE_WORD_CHUNK_MS  = 80   # Audio chunk size fed to wake word model (ms)

# ─── Whisper STT (local) ──────────────────────────────────────────────────────
# Sizes: tiny, base, small, medium, large  (larger = more accurate, slower)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# ─── Ollama LLM (local) ───────────────────────────────────────────────────────
OLLAMA_HOST  = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")

# ─── Piper TTS (local) ────────────────────────────────────────────────────────
# Download a model from: https://github.com/rhasspy/piper/releases
# Place the .onnx file (and its .onnx.json config) in the models/ folder.
# Example: models/en_US-lessac-medium.onnx
PIPER_MODEL_PATH = os.getenv(
    "PIPER_MODEL_PATH",
    "models/en_US-lessac-medium.onnx"
)

# ─── Behaviour ────────────────────────────────────────────────────────────────
MAX_AUDIO_WAIT_S  = 15    # Max seconds to wait for ESP32 audio stream to finish
MAX_TTS_WAIT_S    = 30    # Max seconds to wait for ESP32 to finish playing TTS
MAX_HISTORY_TURNS = 10    # Number of conversation turns to keep in context

# No-PC-mic mode: auto-trigger ESP32 capture instead of requiring Enter.
AUTO_TRIGGER_INTERVAL_S = float(os.getenv("AUTO_TRIGGER_INTERVAL_S", "2.0"))

# When true, keep ESP32 speaker muted and skip sending TTS audio.
# This is useful while debugging STT/LLM without speaker noise.
DISABLE_SPEAKER_OUTPUT = os.getenv("DISABLE_SPEAKER_OUTPUT", "1").strip().lower() in (
    "1", "true", "yes", "on"
)
