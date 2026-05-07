import queue
import time

import whisper

from config import (
    CMD_PROCESSING,
    CMD_SPK_UNMUTE,
    CMD_START,
    CMD_STOP,
    CMD_SUCCESS,
    MAX_TTS_WAIT_S,
    OLLAMA_MODEL,
    PIPER_MODEL_PATH,
    SERIAL_PORT,
    STATUS_STREAM_STOP,
    STATUS_TTS_DONE,
    STATUS_VAD_SILENCE,
    WHISPER_MODEL,
)
from pipeline import get_llm_response, load_piper, synthesize_speech, transcribe
from serial_handler import SerialHandler


def wait_for_any(q: queue.Queue, tokens: list[str], timeout_s: float) -> str | None:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            line = q.get(timeout=0.5)
            print(line, flush=True)
            if any(tok in line for tok in tokens):
                return line
        except queue.Empty:
            pass
    return None


def collect_audio(serial: SerialHandler, timeout_s: float) -> bytes:
    chunks: list[bytes] = []
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            line = serial.status_queue.get_nowait()
            print(line, flush=True)
            if STATUS_STREAM_STOP in line or STATUS_VAD_SILENCE in line:
                break
        except queue.Empty:
            pass

        try:
            chunks.append(serial.audio_queue.get(timeout=0.1))
        except queue.Empty:
            pass

    return b"".join(chunks)


def main() -> None:
    print("Loading Whisper...", flush=True)
    whisper_model = whisper.load_model(WHISPER_MODEL)
    print(f"Whisper ready: {WHISPER_MODEL}", flush=True)

    print("Loading Piper...", flush=True)
    piper_voice = load_piper(PIPER_MODEL_PATH)
    print("Piper ready", flush=True)

    serial = SerialHandler(port=SERIAL_PORT)
    serial.connect()

    print("Connected. Starting one full turn now.", flush=True)
    serial.send_command(CMD_START)

    print("Speak into ESP mic for ~5 seconds...", flush=True)
    raw_audio = collect_audio(serial, timeout_s=15.0)
    if not raw_audio:
        print("No audio captured. Sending STOP.", flush=True)
        serial.send_command(CMD_STOP)
        serial.disconnect()
        return

    print(f"Captured bytes: {len(raw_audio)}", flush=True)

    serial.send_command(CMD_PROCESSING)
    text = transcribe(whisper_model, raw_audio)
    print(f"Transcript: {text!r}", flush=True)
    if not text.strip():
        print("Empty transcript; ending test.", flush=True)
        serial.disconnect()
        return

    print(f"Querying Ollama model {OLLAMA_MODEL}...", flush=True)
    reply = get_llm_response(text)
    print(f"LLM reply: {reply!r}", flush=True)

    tts_pcm = synthesize_speech(reply, piper_voice)
    print(f"TTS bytes: {len(tts_pcm)}", flush=True)

    serial.send_command(CMD_SUCCESS)
    serial.send_command(CMD_SPK_UNMUTE)
    serial.send_tts_audio(tts_pcm)
    print("TTS sent; waiting for TTS_DONE...", flush=True)

    done = wait_for_any(serial.status_queue, [STATUS_TTS_DONE], timeout_s=MAX_TTS_WAIT_S)
    print(f"TTS_DONE received: {bool(done)}", flush=True)

    serial.disconnect()


if __name__ == "__main__":
    main()
