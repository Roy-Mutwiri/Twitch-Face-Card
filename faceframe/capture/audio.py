"""Audio output for the direct stream: WASAPI via PortAudio (sounddevice, MIT).

The callback asks the source for exactly the samples that should be HEARD at
the moment this buffer reaches the speakers (callback time + device latency),
using the same playback clock that schedules the video -> lip sync by design.
FaceFrame then shows up as its own app in the Windows Volume Mixer (named
"FaceFrame" via the WASAPI session display name) and its output device can be
chosen here or per app in Windows Settings.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import numpy as np

log = logging.getLogger("faceframe.audio")

RATE = 48000
CHANNELS = 2


def output_devices() -> list[tuple[int, str]]:
    """(index, name) of WASAPI output devices; index -1 = Windows default."""
    import sounddevice as sd
    out = [(-1, "Windows default output")]
    apis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and "WASAPI" in apis[d["hostapi"]]["name"]:
            out.append((i, d["name"]))
    return out


def default_wasapi_output() -> int | None:
    import sounddevice as sd
    for a in sd.query_hostapis():
        if "WASAPI" in a["name"]:
            return a["default_output_device"]
    return None


class AudioOutput:
    def __init__(self, provider, device: int = -1) -> None:
        self.provider = provider          # object with read_audio(n_frames, dac_wall) -> float32 (n, 2)
        self.device = device
        self._stream = None
        self.error = ""
        self.latency_s = 0.0
        self.underruns = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        import sounddevice as sd
        self.stop()
        dev = default_wasapi_output() if self.device is None or self.device < 0 else self.device
        try:
            self._stream = sd.OutputStream(samplerate=RATE, channels=CHANNELS, dtype="float32", device=dev,
                                           latency="low", callback=self._callback)
            self._stream.start()
            self.latency_s = float(self._stream.latency)
            self.error = ""
            log.info("audio output started on device %s (latency %.0f ms)", dev, self.latency_s * 1000)
            threading.Thread(target=self._name_session, daemon=True).start()
        except Exception as e:
            self.error = f"audio output failed: {e}"
            log.warning(self.error)
            self._stream = None

    def _callback(self, outdata, frames, time_info, status) -> None:
        if status.output_underflow:
            self.underruns += 1
        dac_wall = time.perf_counter() + self.latency_s
        try:
            outdata[:] = self.provider.read_audio(frames, dac_wall)
        except Exception:
            outdata.fill(0)

    def _name_session(self) -> None:
        """Give our WASAPI session the display name 'FaceFrame' in the Volume Mixer."""
        try:
            import comtypes
            comtypes.CoInitialize()
            from pycaw.pycaw import AudioUtilities
            for _ in range(20):
                for s in AudioUtilities.GetAllSessions():
                    if s.Process is not None and s.Process.pid == os.getpid():
                        s._ctl.SetDisplayName("FaceFrame", None)
                        self.session_name = s._ctl.GetDisplayName()
                        log.info("volume mixer session named %r", self.session_name)
                        return
                time.sleep(0.25)
        except Exception as e:
            log.info("could not name the audio session: %s", e)

    def set_device(self, device: int) -> None:
        self.device = device
        if self._stream is not None:
            self.start()

    def stop(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._stream is not None and self._stream.active
