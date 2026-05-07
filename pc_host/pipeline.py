"""
pipeline.py — STT → LLM → TTS Pipeline (Fully Local)
======================================================
Three stages run sequentially for each voice command:

  1. transcribe()        — Whisper (local) converts ESP32 mic audio → text
  2. get_llm_response()  — Ollama (local) generates a spoken-word reply
  3. synthesize_speech() — Piper TTS (local) converts reply → 16kHz PCM

No internet connection required. All processing happens on this PC.

Setup:
  - Ollama must be running:  https://ollama.com  →  ollama pull llama3.2
  - Piper model must be downloaded into the models/ folder:
      https://github.com/rhasspy/piper/releases
      e.g. en_US-lessac-medium.onnx  +  en_US-lessac-medium.onnx.json
"""

import logging
from math import gcd

import numpy as np
import ollama
import whisper
from piper.voice import PiperVoice
from scipy.signal import resample_poly

from config import (
    MAX_HISTORY_TURNS,
    OLLAMA_HOST,
    OLLAMA_MODEL,
    PIPER_MODEL_PATH,
    SAMPLE_RATE,
)

log = logging.getLogger(__name__)

# ─── Conversation history ─────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are a helpful voice assistant on an ESP32 device. "
    "Reply in EXACTLY ONE short sentence — no more. "
    "Do not use markdown, bullet points, or numbered lists; "
    "your response will be read aloud by a text-to-speech engine."
)

_history: list[dict] = []


# ─── Piper voice — loaded once at startup ─────────────────────────────────────
_piper_voice: PiperVoice | None = None


def load_piper(model_path: str = PIPER_MODEL_PATH) -> PiperVoice:
    """
    Load the Piper TTS voice model from disk.
    Call this once at startup and pass the returned voice to synthesize_speech().

    Args:
        model_path: Path to the .onnx model file.
                    The .onnx.json config must be in the same directory.
    Returns:
        A loaded PiperVoice instance.
    """
    global _piper_voice
    log.info("Loading Piper TTS model: %s", model_path)
    _piper_voice = PiperVoice.load(model_path)
    log.info(
        "Piper ready — sample rate: %d Hz", _piper_voice.config.sample_rate
    )
    return _piper_voice


# ─── 1. Speech-to-Text ───────────────────────────────────────────────────────

def transcribe(model: whisper.Whisper, pcm_bytes: bytes) -> str:
    """
    Transcribe raw 16kHz 16-bit mono PCM bytes using a local Whisper model.

    Args:
        model:     A loaded whisper.Whisper instance.
        pcm_bytes: Raw PCM audio bytes captured from the ESP32.

    Returns:
        Transcribed text string (may be empty if audio was silent).
    """
    if not pcm_bytes:
        return ""

    # Whisper expects float32 normalized to [-1, 1].
    # Precondition captured audio to improve STT on low-amplitude mic signals.
    audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    # Remove DC offset that can appear on I2S MEMS mics.
    audio_np = audio_np - float(np.mean(audio_np))

    peak = float(np.max(np.abs(audio_np))) if audio_np.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio_np)))) if audio_np.size else 0.0
    log.info("STT input levels: rms=%.5f peak=%.5f", rms, peak)

    # Normalize quieter captures to a workable level for Whisper.
    if peak > 1e-6 and peak < 0.25:
        gain = min(0.9 / peak, 20.0)
        audio_np = np.clip(audio_np * gain, -1.0, 1.0)
        log.info("Applied STT pre-gain: x%.2f", gain)

    result = model.transcribe(audio_np, fp16=False, language="en")
    text: str = result.get("text", "").strip()
    log.debug("Whisper transcript: %r", text)
    return text


# ─── 2. LLM — Ollama ─────────────────────────────────────────────────────────

def get_llm_response(user_text: str) -> str:
    """
    Send the transcribed user text to a local Ollama model and return the reply.
    Maintains a rolling conversation history of MAX_HISTORY_TURNS turns.

    Args:
        user_text: The transcribed voice command.

    Returns:
        The assistant's reply as a plain-text string.
    """
    global _history

    _history.append({"role": "user", "content": user_text})

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}] + _history

    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=messages,
        options={"temperature": 0.7},
    )

    reply: str = response["message"]["content"].strip()
    _history.append({"role": "assistant", "content": reply})

    # Trim history to avoid unbounded growth
    if len(_history) > MAX_HISTORY_TURNS * 2:
        _history = _history[-(MAX_HISTORY_TURNS * 2):]

    log.debug("Ollama reply: %r", reply)
    return reply


def clear_history() -> None:
    """Reset conversation history (e.g. after a long silence or restart)."""
    global _history
    _history = []
    log.info("Conversation history cleared.")


# ─── 3. Text-to-Speech — Piper ───────────────────────────────────────────────

def _resample(pcm_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
    """
    Resample 16-bit mono PCM from from_rate to to_rate using polyphase filtering.
    Piper models typically output at 22050Hz; the ESP32 expects 16000Hz.
    """
    if from_rate == to_rate:
        return pcm_bytes

    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)

    g    = gcd(from_rate, to_rate)
    up   = to_rate   // g
    down = from_rate // g

    resampled = resample_poly(samples, up, down)
    resampled = np.clip(resampled, -32768, 32767).astype(np.int16)
    return resampled.tobytes()


def synthesize_speech(text: str, voice: PiperVoice | None = None) -> bytes:
    """
    Convert text to speech using a local Piper TTS model and resample to 16kHz.

    Piper synthesises audio at the model's native sample rate (commonly 22050Hz).
    This function resamples it to SAMPLE_RATE (16kHz) so the bytes are ready to
    frame and send to the ESP32.

    Args:
        text:  The assistant reply to speak.
        voice: A loaded PiperVoice. Falls back to the module-level loaded voice.

    Returns:
        Raw 16kHz 16-bit mono PCM bytes.
    """
    v = voice or _piper_voice
    if v is None:
        raise RuntimeError(
            "Piper voice not loaded. Call load_piper() before synthesize_speech()."
        )

    # Piper API compatibility:
    # - Older/newer builds may expose either synthesize_stream_raw(text)
    #   or synthesize(text) -> Iterable[AudioChunk].
    pcm_native = b""

    if hasattr(v, "synthesize_stream_raw"):
        raw_chunks: list[bytes] = list(v.synthesize_stream_raw(text))
        pcm_native = b"".join(raw_chunks)
    elif hasattr(v, "synthesize"):
        chunk_bytes: list[bytes] = []
        for chunk in v.synthesize(text):
            if hasattr(chunk, "audio_int16_bytes"):
                chunk_bytes.append(chunk.audio_int16_bytes)
            else:
                arr = chunk.audio_int16_array
                chunk_bytes.append(arr.astype(np.int16).tobytes())
        pcm_native = b"".join(chunk_bytes)
    else:
        raise RuntimeError("Unsupported Piper API: no synthesize method found")

    native_rate = v.config.sample_rate
    if native_rate != SAMPLE_RATE:
        pcm_out = _resample(pcm_native, native_rate, SAMPLE_RATE)
    else:
        pcm_out = pcm_native

    log.debug(
        "Piper TTS: %d bytes @ %dHz → %d bytes @ %dHz",
        len(pcm_native), native_rate, len(pcm_out), SAMPLE_RATE,
    )
    return pcm_out
