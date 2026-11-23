"""Game sound for the recordings: Windows WASAPI loopback (what you hear), written to a WAV file.

Loopback only delivers data while something is playing, so silent gaps are filled with silence
against the wall clock – that keeps the sound in sync with the video.
"""
import threading
import time
import wave


class LoopbackAudio:
    def __init__(self, path):
        self.path = path
        self.ok = False
        self._stop = threading.Event()
        self._thread = None
        self.error = None

    def start(self):
        try:
            import pyaudiowpatch as pyaudio  # Windows only (PortAudio with WASAPI loopback)
        except ImportError as e:
            self.error = f"kein Audio-Modul ({e})"
            return False
        try:
            self._pa = pa = pyaudio.PyAudio()
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            dev = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not dev.get("isLoopbackDevice"):
                for lb in pa.get_loopback_device_info_generator():
                    if dev["name"] in lb["name"]:
                        dev = lb
                        break
                else:
                    self.error = "kein Loopback-Gerät gefunden"
                    return False
            self.rate = int(dev["defaultSampleRate"])
            self.ch = max(1, min(2, int(dev["maxInputChannels"])))
            self.width = 2
            self._wav = wave.open(self.path, "wb")
            self._wav.setnchannels(self.ch)
            self._wav.setsampwidth(self.width)
            self._wav.setframerate(self.rate)
            self._written = 0
            self._t0 = time.monotonic()
            self._lock = threading.Lock()

            def cb(data, frames, _time, _status):
                with self._lock:
                    self._pad_to(time.monotonic() - frames / self.rate)
                    self._wav.writeframes(data)
                    self._written += frames
                return (None, pyaudio.paContinue)
            self._stream = pa.open(format=pyaudio.paInt16, channels=self.ch, rate=self.rate, input=True,
                                   input_device_index=dev["index"], frames_per_buffer=int(self.rate / 50),
                                   stream_callback=cb)
            self._thread = threading.Thread(target=self._silence_filler, daemon=True)
            self._thread.start()
            self.ok = True
            return True
        except Exception as e:  # noqa: BLE001 - never break the video recording because of sound
            self.error = str(e)
            return False

    def _pad_to(self, now):
        """Write silence up to wall-clock position `now` (seconds since start)."""
        want = int((now - self._t0) * self.rate)
        gap = want - self._written
        if gap > self.rate // 10:  # more than 100 ms missing -> silence
            self._wav.writeframes(b"\x00" * gap * self.ch * self.width)
            self._written += gap

    def _silence_filler(self):
        while not self._stop.wait(0.5):
            with self._lock:
                self._pad_to(time.monotonic() - 0.3)

    def stop(self):
        if not self.ok:
            return False
        self._stop.set()
        try:
            self._stream.stop_stream()
            self._stream.close()
            self._pa.terminate()
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self._pad_to(time.monotonic())
            self._wav.close()
        self.ok = False
        return True
