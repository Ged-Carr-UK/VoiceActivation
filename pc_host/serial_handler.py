"""
serial_handler.py — ESP32 Serial Communication
================================================
Handles the mixed binary/ASCII protocol between the PC and the ESP32.

ESP32 → PC stream:
  - ASCII status lines:  "EAS_STATUS:READY\n", "EAS_STATUS:STREAM_STOP\n", etc.
  - Binary PCM packets:  PCM_HEADER + raw int16 PCM bytes + PCM_FOOTER

PC → ESP32 stream:
  - ASCII commands:      "CMD:START\n", "CMD:STOP\n", etc.
  - Binary TTS packets:  TTS_HEADER + raw int16 PCM bytes + TTS_FOOTER
"""

import logging
import queue
import threading
import time
from array import array
import sys

import serial

from config import PCM_FOOTER, PCM_HEADER, SAMPLE_RATE, SERIAL_BAUD, SERIAL_PORT

log = logging.getLogger(__name__)

_PCM_HEADER_FIRST = PCM_HEADER[0]   # 0xEA — marks start of a binary packet
_MAX_PACKET_BYTES = 512 * 1024      # 512 KB safety cap per packet


class SerialHandler:
    def __init__(self, port: str = SERIAL_PORT, baud: int = SERIAL_BAUD):
        self.port = port
        self.baud = baud
        self._ser: serial.Serial | None = None
        self._running = False
        self._thread: threading.Thread | None = None

        # Queues filled by the background reader thread
        self.status_queue: queue.Queue[str] = queue.Queue()
        self.audio_queue:  queue.Queue[bytes] = queue.Queue()

    # ─── Public interface ─────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the serial port and start the background reader."""
        self._ser = serial.Serial(self.port, self.baud, timeout=1)
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, name="serial-rx", daemon=True)
        self._thread.start()
        log.info("Connected to %s @ %d baud", self.port, self.baud)

    def disconnect(self) -> None:
        """Stop the reader thread and close the port."""
        self._running = False
        if self._ser and self._ser.is_open:
            self._ser.close()
        log.info("Serial port closed.")

    def send_command(self, cmd: str) -> None:
        """Send an ASCII command string followed by a newline."""
        if self._ser and self._ser.is_open:
            self._ser.write((cmd + "\n").encode("ascii"))
            log.debug("TX CMD: %s", cmd)

    def send_tts_audio(self, pcm_bytes: bytes) -> None:
        """Frame PCM audio with TTS header/length and write to serial."""
        import struct
        from config import TTS_HEADER
        if self._ser and self._ser.is_open:
            # Smooth boundaries to reduce start/end squeaks (amp pop + abrupt waveform cuts).
            # Apply asymmetric fades + silence padding around the utterance.
            samples = array('h')
            samples.frombytes(pcm_bytes)
            if sys.byteorder != 'little':
                samples.byteswap()

            total = len(samples)
            fade_in_samples = min(int(0.120 * SAMPLE_RATE), total)      # 120 ms (mask start transient)
            fade_out_samples = min(int(0.004 * SAMPLE_RATE), total)     # 4 ms (minimal tail softening)

            if fade_in_samples > 0:
                denom_in = float(fade_in_samples)
                for i in range(fade_in_samples):
                    g = i / denom_in
                    samples[i] = int(samples[i] * g)

            if fade_out_samples > 0:
                denom_out = float(fade_out_samples)
                for i in range(fade_out_samples):
                    g = i / denom_out
                    j = total - 1 - i
                    samples[j] = int(samples[j] * g)

            pre_pad_samples = int(0.180 * SAMPLE_RATE)   # 180 ms to isolate start transient in silence
            post_pad_samples = int(0.220 * SAMPLE_RATE)  # 220 ms to keep endings from feeling chopped
            pre_pad = b"\x00\x00" * pre_pad_samples
            post_pad = b"\x00\x00" * post_pad_samples
            smoothed_pcm = pre_pad + samples.tobytes() + post_pad

            # New protocol: HEADER (4) + LENGTH (4) + PCM
            length_bytes = struct.pack('<I', len(smoothed_pcm))
            payload = TTS_HEADER + length_bytes + smoothed_pcm
            # Pace transmission close to playback rate: ~32 KB/s for 16 kHz mono int16 PCM.
            # This avoids underruns (too slow) and ring overruns (too fast).
            chunk_size = 288
            inter_chunk_s = 0.010
            for i in range(0, len(payload), chunk_size):
                self._ser.write(payload[i:i + chunk_size])
                time.sleep(inter_chunk_s)
            log.debug("TX TTS: %d bytes (smoothed to %d bytes)", len(pcm_bytes), len(smoothed_pcm))

    # ─── Background reader ────────────────────────────────────────────────────

    def _read_exact(self, n: int) -> bytes:
        """Read exactly n bytes, blocking until available."""
        buf = b""
        while len(buf) < n and self._running:
            chunk = self._ser.read(n - len(buf))
            if chunk:
                buf += chunk
        return buf

    def _read_until_footer(self) -> bytes:
        """
        Read bytes from serial until PCM_FOOTER is found.
        Returns the payload (everything before the footer).
        """
        buf = bytearray()
        flen = len(PCM_FOOTER)

        while self._running:
            b = self._ser.read(1)
            if not b:
                continue
            buf.extend(b)

            # Check whether the footer appears at the tail of the buffer
            if len(buf) >= flen and bytes(buf[-flen:]) == PCM_FOOTER:
                return bytes(buf[:-flen])

            if len(buf) > _MAX_PACKET_BYTES:
                log.warning("PCM packet exceeded max size — discarding")
                return b""

        return b""

    def _read_ascii_line(self, first_byte: bytes) -> str:
        """Read a '\n'-terminated ASCII line given the first byte was already consumed."""
        buf = bytearray(first_byte)
        while self._running:
            b = self._ser.read(1)
            if not b:
                continue
            if b == b"\n":
                break
            buf.extend(b)
        return buf.decode("ascii", errors="replace").strip()

    def _read_loop(self) -> None:
        """
        Background thread: continuously reads from serial and classifies each
        incoming byte as either the start of:
          - A binary PCM audio packet (starts with 0xEA = _PCM_HEADER_FIRST)
          - An ASCII status line (any other byte)
        """
        while self._running:
            try:
                b = self._ser.read(1)
                if not b:
                    continue

                if b[0] == _PCM_HEADER_FIRST:
                    # Potential binary packet — read 3 more bytes to complete header
                    rest = self._read_exact(3)
                    header = b + rest

                    if header == PCM_HEADER:
                        payload = self._read_until_footer()
                        if payload:
                            self.audio_queue.put(payload)
                            log.debug("RX PCM: %d bytes", len(payload))
                    else:
                        log.warning("Unknown binary header: %s", header.hex())

                else:
                    # ASCII status line
                    line = self._read_ascii_line(b)
                    if line:
                        self.status_queue.put(line)
                        log.info("ESP32: %s", line)

            except serial.SerialException as exc:
                if self._running:
                    log.error("Serial error: %s", exc)
                break
            except Exception as exc:
                if self._running:
                    log.error("Unexpected read error: %s", exc)
