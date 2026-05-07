import queue
import time

from pipeline import load_piper, synthesize_speech
from serial_handler import SerialHandler


def wait_for(status_queue: queue.Queue, token: str, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        try:
            line = status_queue.get(timeout=0.5)
            print(line, flush=True)
            if token in line:
                return True
        except queue.Empty:
            pass
    return False


def main() -> None:
    s = SerialHandler(port="COM3")
    s.connect()

    print("Waiting for READY...", flush=True)
    ready = wait_for(s.status_queue, "EAS_STATUS:READY", 10.0)
    print(f"READY={ready}", flush=True)

    s.send_command("CMD:SPK_UNMUTE")
    s.send_command("CMD:SPK_VOLUME:45")
    time.sleep(0.2)

    voice = load_piper()
    pcm = synthesize_speech(
        "Speaker verification test. If you hear this, reply yes.",
        voice,
    )
    print(f"Sending {len(pcm)} bytes", flush=True)
    s.send_tts_audio(pcm)

    playing = wait_for(s.status_queue, "EAS_STATUS:TTS_PLAYING", 5.0)
    done = wait_for(s.status_queue, "EAS_STATUS:TTS_DONE", 20.0)
    print(f"TTS_PLAYING={playing} TTS_DONE={done}", flush=True)

    s.send_command("CMD:SPK_MUTE")
    s.disconnect()


if __name__ == "__main__":
    main()
