"""
main.py — EAS Voice Controller PC Host (Fully Local)
======================================================
Orchestrates the full voice pipeline — no internet required:

  1. Always-on wake word detection using the PC microphone (openwakeword)
  2. On detection  → sends CMD:START to ESP32, collects mic audio via serial
  3. STT           → Whisper (local) transcribes the audio
  4. LLM           → Ollama (local) generates a spoken reply
  5. TTS           → Piper TTS (local) synthesises speech → resampled to 16kHz
  6. Playback      → TTS audio sent to ESP32 over serial; ESP32 plays through speaker
  7. Return to (1)

Wake word: "hey jarvis"  (change WAKE_WORD_MODEL in .env)

Prerequisites:
  - Ollama running:   https://ollama.com  →  ollama pull llama3.2
  - Piper model in:   pc_host/models/en_US-lessac-medium.onnx  (+ .onnx.json)
    Download from:    https://github.com/rhasspy/piper/releases
"""

import logging
import queue
import sys
import threading
import time

import numpy as np
import sounddevice as sd
import whisper
from openwakeword.model import Model as WakeWordModel

from config import (
    AUDIO_INPUT_DEVICE,
    AUTO_TRIGGER_INTERVAL_S,
    CMD_ERROR,
    CMD_PROCESSING,
    CMD_SPK_MUTE,
    CMD_SPK_UNMUTE,
    CMD_START,
    CMD_STOP,
    CMD_SUCCESS,
    DISABLE_SPEAKER_OUTPUT,
    MAX_AUDIO_WAIT_S,
    MAX_TTS_WAIT_S,
    OLLAMA_MODEL,
    PIPER_MODEL_PATH,
    SAMPLE_RATE,
    SERIAL_PORT,
    STATUS_READY,
    STATUS_STREAM_STOP,
    STATUS_TTS_DONE,
    STATUS_VAD_SILENCE,
    WAKE_WORD_CHUNK_MS,
    WAKE_WORD_MODEL,
    WAKE_WORD_THRESHOLD,
    WHISPER_MODEL,
)
from pipeline import (
    clear_history,
    get_llm_response,
    load_piper,
    synthesize_speech,
    transcribe,
)
from serial_handler import SerialHandler

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def select_input_device() -> int | None:
    """
    Resolve an input device for sounddevice.
    Returns an integer device index, or None to use the OS default.
    """
    requested = AUDIO_INPUT_DEVICE
    devices = sd.query_devices()

    # 1) Explicit index in .env
    if requested:
        if requested.isdigit():
            idx = int(requested)
            if 0 <= idx < len(devices) and devices[idx].get("max_input_channels", 0) > 0:
                log.info("Using input device index %d: %s", idx, devices[idx].get("name", "unknown"))
                return idx
            log.warning("AUDIO_INPUT_DEVICE index %s is not a valid input device.", requested)
        else:
            # 2) Name substring in .env
            needle = requested.lower()
            for idx, dev in enumerate(devices):
                name = str(dev.get("name", ""))
                if dev.get("max_input_channels", 0) > 0 and needle in name.lower():
                    log.info("Using input device '%s' at index %d", name, idx)
                    return idx
            log.warning("AUDIO_INPUT_DEVICE '%s' not found among input devices.", requested)

    # 3) Try OS default first (None)
    try:
        default_input, _ = sd.default.device
        if isinstance(default_input, int) and default_input >= 0:
            log.info("Using OS default input device index %d", default_input)
            return default_input
    except Exception:
        pass

    # 4) Fallback: first device with input channels
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0:
            log.info("No OS default input. Falling back to index %d: %s", idx, dev.get("name", "unknown"))
            return idx

    return None


def drain_queue(q: queue.Queue) -> None:
    """Discard all items currently in a queue."""
    while not q.empty():
        try:
            q.get_nowait()
        except queue.Empty:
            break


def wait_for_status(
    status_queue: queue.Queue,
    tokens: list[str],
    timeout: float,
) -> str | None:
    """
    Block until a status line containing any of 'tokens' arrives, or timeout.
    Returns the matching status line, or None on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            line = status_queue.get(timeout=min(remaining, 0.5))
            if any(tok in line for tok in tokens):
                return line
        except queue.Empty:
            pass
    return None


def collect_audio(serial: SerialHandler, timeout: float) -> bytes:
    """
    Collect PCM audio packets from the ESP32 until the stream ends or timeout.
    Returns all chunks concatenated as a single bytes object.
    """
    chunks: list[bytes] = []
    stream_done = threading.Event()

    def _status_watcher() -> None:
        while not stream_done.is_set():
            try:
                line = serial.status_queue.get(timeout=0.5)
                if STATUS_STREAM_STOP in line or STATUS_VAD_SILENCE in line:
                    log.info("ESP32: %s", line)
                    stream_done.set()
            except queue.Empty:
                pass

    watcher = threading.Thread(target=_status_watcher, daemon=True)
    watcher.start()

    deadline = time.monotonic() + timeout
    while not stream_done.is_set() and time.monotonic() < deadline:
        try:
            chunk = serial.audio_queue.get(timeout=0.1)
            chunks.append(chunk)
        except queue.Empty:
            pass

    if not stream_done.is_set():
        log.warning("Audio collection timed out — sending CMD:STOP")
        serial.send_command(CMD_STOP)

    stream_done.set()
    return b"".join(chunks)


def main() -> None:
    # ── Load models ───────────────────────────────────────────────────────────
    log.info("Loading Whisper '%s' model (first run downloads it)...", WHISPER_MODEL)
    whisper_model = whisper.load_model(WHISPER_MODEL)
    log.info("Whisper ready.")

    try:
        piper_voice = load_piper(PIPER_MODEL_PATH)
    except Exception as exc:
        log.error("Failed to load Piper model: %s", exc)
        log.error(
            "Download a model from https://github.com/rhasspy/piper/releases\n"
            "and place the .onnx and .onnx.json files in pc_host/models/\n"
            "then set PIPER_MODEL_PATH in .env to point to the .onnx file."
        )
        sys.exit(1)

    log.info("Loading wake word model '%s' (first run downloads it)...", WAKE_WORD_MODEL)
    ww_model = WakeWordModel(
        wakeword_models=[WAKE_WORD_MODEL],
        inference_framework="onnx",
    )
    log.info("Wake word model ready.")

    log.info("Ollama model: %s  (ensure 'ollama serve' is running)", OLLAMA_MODEL)

    # ── Connect to ESP32 ──────────────────────────────────────────────────────
    serial = SerialHandler(port=SERIAL_PORT)
    try:
        serial.connect()
    except Exception as exc:
        log.error("Cannot open serial port %s: %s", SERIAL_PORT, exc)
        log.error("Check SERIAL_PORT in .env and that the ESP32 is plugged in.")
        sys.exit(1)

    log.info("Waiting for ESP32 to boot...")
    result = wait_for_status(serial.status_queue, [STATUS_READY], timeout=15)
    if result:
        log.info("ESP32 ready.")
    else:
        log.warning("ESP32 did not send READY within 15s — continuing anyway.")

    if DISABLE_SPEAKER_OUTPUT:
        serial.send_command(CMD_SPK_MUTE)
        log.info("Speaker output disabled: ESP32 muted and TTS send path bypassed.")
    else:
        # Keep speaker path enabled once at startup to avoid per-utterance unmute pops.
        serial.send_command(CMD_SPK_UNMUTE)

    def set_ready_indicator() -> None:
        """Show amber/yellow while waiting for the next capture cycle."""
        serial.send_command(CMD_PROCESSING)

    # ── Wake word detection ───────────────────────────────────────────────────
    wake_event = threading.Event()
    chunk_samples = int(SAMPLE_RATE * WAKE_WORD_CHUNK_MS / 1000)   # 1280 @ 16kHz

    def _audio_callback(
        indata: np.ndarray,
        frames: int,
        time_info,
        status,
    ) -> None:
        if status:
            log.debug("Mic status: %s", status)
        audio_int16 = (indata[:, 0] * 32767).astype(np.int16)
        predictions: dict[str, float] = ww_model.predict(audio_int16)
        score = predictions.get(WAKE_WORD_MODEL, 0.0)
        if score >= WAKE_WORD_THRESHOLD:
            log.info("Wake word '%s' detected (score=%.2f)", WAKE_WORD_MODEL, score)
            wake_event.set()

    wake_word_display = WAKE_WORD_MODEL.replace("_", " ").title()
    log.info("Listening for wake word: '%s'  (say it to start)", wake_word_display)

    input_device = select_input_device()

    def process_turn() -> None:
        """Run one full ESP32 capture -> STT -> LLM -> TTS turn."""
        log.info("--- Voice command triggered ---")
        drain_queue(serial.audio_queue)
        drain_queue(serial.status_queue)

        # Tell ESP32 to start streaming
        serial.send_command(CMD_START)

        # Collect mic audio from ESP32
        raw_audio = collect_audio(serial, timeout=MAX_AUDIO_WAIT_S)

        if not raw_audio:
            log.warning("No audio captured — returning to trigger mode.")
            serial.send_command(CMD_ERROR)
            set_ready_indicator()
            log.info("Listening for trigger...")
            return

        log.info(
            "Captured %.2f s of audio (%d bytes)",
            len(raw_audio) / (SAMPLE_RATE * 2), len(raw_audio),
        )

        # STT
        serial.send_command(CMD_PROCESSING)
        log.info("Transcribing...")
        transcript = transcribe(whisper_model, raw_audio)
        log.info("You said: %r", transcript)

        if not transcript.strip():
            log.warning("Empty transcript — returning to trigger mode.")
            serial.send_command(CMD_ERROR)
            set_ready_indicator()
            log.info("Listening for trigger...")
            return

        # LLM
        log.info("Thinking (Ollama / %s)...", OLLAMA_MODEL)
        try:
            reply = get_llm_response(transcript)
        except Exception as exc:
            log.error("Ollama error: %s", exc)
            log.error("Is Ollama running? Try: ollama serve")
            serial.send_command(CMD_ERROR)
            set_ready_indicator()
            log.info("Listening for trigger...")
            return

        log.info("Assistant: %r", reply)

        # TTS -> ESP32 (optional)
        if DISABLE_SPEAKER_OUTPUT:
            serial.send_command(CMD_SUCCESS)
            log.info("Speaker output disabled — skipping TTS synthesis/playback.")
        else:
            log.info("Synthesising speech (Piper)...")
            try:
                tts_pcm = synthesize_speech(reply, piper_voice)
            except Exception as exc:
                log.error("Piper TTS error: %s", exc)
                serial.send_command(CMD_ERROR)
                set_ready_indicator()
                log.info("Listening for trigger...")
                return

            serial.send_command(CMD_SUCCESS)
            serial.send_tts_audio(tts_pcm)
            log.info("Sent %d bytes of TTS audio to ESP32.", len(tts_pcm))

            # Wait for playback to finish
            result = wait_for_status(
                serial.status_queue,
                [STATUS_TTS_DONE],
                timeout=MAX_TTS_WAIT_S,
            )
            if result:
                log.info("Playback complete.")
            else:
                log.warning(
                    "TTS_DONE not received within %ds — continuing.", MAX_TTS_WAIT_S
                )

        set_ready_indicator()
        log.info("Listening for trigger...")

    input_devices = [d for d in sd.query_devices() if d.get("max_input_channels", 0) > 0]
    if not input_devices:
        log.warning("No PC microphone device found. Falling back to auto-trigger mode.")
        log.warning(
            "Auto-trigger interval: %.1fs (set AUTO_TRIGGER_INTERVAL_S in .env).",
            AUTO_TRIGGER_INTERVAL_S,
        )
        set_ready_indicator()
        while True:
            time.sleep(max(0.2, AUTO_TRIGGER_INTERVAL_S))
            process_turn()
        return

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=chunk_samples,
            callback=_audio_callback,
            device=input_device,
        )
    except Exception as exc:
        log.error("Failed to open input stream: %s", exc)
        log.error("Available input devices:")
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0:
                log.error("  [%d] %s", idx, dev.get("name", "unknown"))
        log.error("Set AUDIO_INPUT_DEVICE in .env to an index or a device name substring.")
        sys.exit(1)

    with stream:
        while True:
            # ── Wait for wake word ────────────────────────────────────────────
            wake_event.wait()
            wake_event.clear()
            process_turn()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted — shutting down.")
        sys.exit(0)
