"""Audio read path: callback timing jitter must not cause skips; real clock jumps must resync."""

import numpy as np

from faceframe.capture.direct import DirectStreamSource

SR = 48000


def _source_with_tone(seconds=20.0):
    src = DirectStreamSource("somechannel")
    t = np.arange(int(seconds * SR)) / SR
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo = np.stack([tone, tone], 1)
    for i in range(0, len(stereo), 1024):
        src._audio.append((i / SR, stereo[i:i + 1024]))
    src._base = (0.0, 100.0)          # pts 0 is heard at wall 100.0
    return src


def test_jittery_callbacks_play_continuously_without_resync():
    src = _source_with_tone()
    rng = np.random.default_rng(1)
    n = 480
    wall = 100.0
    chunks = []
    for i in range(1500):                                  # 15 s of 10 ms callbacks
        jitter = rng.uniform(-0.04, 0.09) if i % 50 == 0 else rng.uniform(-0.004, 0.004)
        chunks.append(src.read_audio(n, wall + jitter))
        wall += n / SR
    out = np.concatenate(chunks)[:, 0]
    assert src.audio_resyncs == 0
    # continuity: a 440 Hz tone at 0.5 amplitude never steps more than its max slope
    max_step = 0.5 * 2 * np.pi * 440 / SR * 1.05
    assert np.abs(np.diff(out[SR:])).max() < max_step


def test_slow_drift_is_corrected_smoothly():
    src = _source_with_tone()
    n = 480
    wall = 100.0
    for _ in range(1000):
        src.read_audio(n, wall)
        wall += n / SR * 1.002                              # device clock 0.2 % fast
    target = src.media_time(wall)
    assert src.audio_resyncs == 0
    assert abs(src._audio_cursor - target) < 0.03


def test_clock_jump_resyncs_once():
    src = _source_with_tone()
    n = 480
    src.read_audio(n, 100.0)
    src.read_audio(n, 103.0)                                # 3 s jump (delay change / re-anchor)
    assert src.audio_resyncs == 1
    assert abs(src._audio_cursor - (3.0 + n / SR)) < 0.002


def test_running_is_reported_again_after_reconnect():
    import av
    from faceframe.core.source_base import SourceState

    src = DirectStreamSource("somechannel")
    states = []

    class Sink:
        def on_status(self, st):
            states.append(st.state)
    src._sink = Sink()
    f = av.VideoFrame.from_ndarray(np.zeros((720 * 3 // 2, 1280), np.uint8), format="yuv420p")
    src._prepare(f, 0.0)
    src._status(SourceState.STALLED, "stream ended")
    src._announced = False                      # what the decode loop does after every (re)open
    src._prepare(f, 1.0)
    assert states == [SourceState.RUNNING, SourceState.STALLED, SourceState.RUNNING]
    assert src.state == SourceState.RUNNING
