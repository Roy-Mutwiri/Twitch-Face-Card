"""Pipeline contracts with a deterministic fake source and fake detector.
(These prove the pipeline logic only; real Windows capture is validated by the
tools/ scripts against live windows and is NOT claimed by these tests.)"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
import pytest

from faceframe.core.frame import ClockDomain, Frame, Overview, SourceKind, now_ns
from faceframe.core.geometry import Rect
from faceframe.core.pipeline import Pipeline, PipelineConfig
from faceframe.core.source_base import SourceState, SourceStatus
from faceframe.core.tracking import Detection, TrackState

MODELS = Path(__file__).resolve().parents[1] / "models"
PANEL = Rect(100, 50, 320, 180)


class FakeDetector:
    """Returns the box of the bright square drawn by FakeSource (panel coords)."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.enabled = True
        self.extra: list[Rect] = []
        self.last_ms = 0.1

    def detect(self, img):
        if self.delay:
            time.sleep(self.delay)
        if not self.enabled:
            return []
        ys, xs = np.where(img[..., 1] > 240)
        out = []
        if len(xs):
            out.append(Detection(Rect(xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1), 0.95))
        out += [Detection(r, 0.9) for r in self.extra]
        return out


class FakeSource:
    kind = SourceKind.TEST

    def __init__(self, name="fake", size=(640, 360), fps=60):
        self.source_id = f"test:{name}"
        self.session_id = 0
        self.size = size
        self.fps = fps
        self.panel = None
        self.sink = None
        self._stop = threading.Event()
        self.seq = 0
        self.face_x = 120.0
        self.stopped = False

    def describe(self):
        return {}

    def set_panel(self, p):
        self.panel = p

    def request_overview(self):
        pass

    def start(self, sink):
        self.sink = sink
        sink.on_status(SourceStatus(SourceState.RUNNING, "fake running"))
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()
        self.stopped = True

    def frame_image(self):
        w, h = self.size
        img = np.zeros((h, w, 3), np.uint8)
        img[..., 0] = np.linspace(0, 120, w, dtype=np.uint8)[None, :]
        img[..., 2] = 60
        x = int(self.face_x)
        img[90:140, x:x + 40, 1] = 255  # the "face" (panel-relative 20..60 rows)
        return img

    def _run(self):
        t_ov = 0
        while not self._stop.is_set():
            img = self.frame_image()
            t = now_ns()
            if time.perf_counter() - t_ov > 0.5:
                t_ov = time.perf_counter()
                self.sink.on_overview(Overview(self.source_id, self.session_id, t, self.size, img.copy(), 1.0,
                                               self.kind))
            p = self.panel
            if p is not None:
                x, y, w, h = p.to_int()
                self.seq += 1
                self.sink.on_frame(Frame(self.source_id, self.session_id, self.seq, t, ClockDomain.HOST_PERF, t,
                                         self.kind, self.size, Rect(x, y, w, h),
                                         np.ascontiguousarray(img[y:y + h, x:x + w])))
            time.sleep(1 / self.fps)


@pytest.fixture
def pipe():
    p = Pipeline(MODELS, PipelineConfig(out_w=640, out_h=360, out_fps=30, detect_hz=15), detector=FakeDetector())
    yield p
    p.shutdown()


def wait_for(cond, timeout=3.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_output_exact_size_rate_and_bounded_queues(pipe):
    src = FakeSource()
    pipe.set_source(src)
    pipe.set_panel(PANEL)
    assert wait_for(lambda: pipe.tracker.single_clear_candidate() is not None)
    pipe.select_detection(pipe.tracker.candidates[0].box)
    sizes = set()
    max_pending = 0
    t0 = time.time()
    seen = 0
    n = 0
    while time.time() - t0 < 1.5:
        o, v = pipe.out_slot.get_newer(seen, timeout=0.2)
        if o is None:
            continue
        seen = v
        n += 1
        sizes.add(o.image.shape)
        max_pending = max(max_pending, pipe.frame_slot.pending, pipe.det_slot.pending)
    assert sizes == {(360, 640, 3)}
    assert max_pending <= 1
    assert 25 <= n / 1.5 <= 33           # ~30 fps output from a 60 fps source
    assert pipe.status()["dropped_process"] > 0  # superseded frames are dropped, not queued
    assert pipe.status()["queue_age_p95"] < 50


def test_source_change_flushes_old_session(pipe):
    a = FakeSource("a")
    pipe.set_source(a)
    pipe.set_panel(PANEL)
    assert wait_for(lambda: pipe.out_slot.peek()[0] is not None)
    b = FakeSource("b")
    pipe.set_source(b)
    assert a.stopped
    sid = pipe.session_id
    assert pipe.tracker.state in (TrackState.SEARCHING, TrackState.SOURCE_UNAVAILABLE)
    assert pipe.tracker.target is None
    pipe.set_panel(PANEL)
    sid = pipe.session_id
    assert wait_for(lambda: (pipe.out_slot.peek()[0] is not None and pipe.out_slot.peek()[0].session_id == sid
                             and not pipe.out_slot.peek()[0].neutral))
    # frames from the old source arriving late are ignored
    stale = Frame("test:a", sid - 5, 999, now_ns(), ClockDomain.HOST_PERF, now_ns(), SourceKind.TEST, (640, 360),
                  PANEL, np.zeros((180, 320, 3), np.uint8))
    before = pipe.frames_in
    pipe.on_frame(stale)
    assert pipe.frames_in == before and pipe.frames_stale_session >= 1


def test_stale_detection_rejected_after_panel_change():
    det = FakeDetector(delay=0.3)
    p = Pipeline(MODELS, PipelineConfig(out_w=320, out_h=180), detector=det)
    try:
        src = FakeSource()
        p.set_source(src)
        p.set_panel(PANEL)
        time.sleep(0.15)                      # detector is now busy on a session-N frame
        p.set_panel(Rect(110, 50, 320, 180))  # new session while detection in flight
        assert wait_for(lambda: p.tracker.stale_rejected >= 1 or p.det_rate.total >= 2, 2.0)
        time.sleep(0.4)
        assert p.tracker.stale_rejected >= 1
    finally:
        p.shutdown()


def test_unavailable_source_gives_neutral_never_old_person(pipe):
    src = FakeSource()
    pipe.set_source(src)
    pipe.set_panel(PANEL)
    assert wait_for(lambda: pipe.tracker.single_clear_candidate() is not None)
    pipe.select_detection(pipe.tracker.candidates[0].box)
    assert wait_for(lambda: pipe.out_slot.peek()[0] is not None and not pipe.out_slot.peek()[0].neutral)
    src._stop.set()  # frames stop
    pipe.on_status(SourceStatus(SourceState.CLOSED, "closed"))
    assert wait_for(lambda: pipe.out_slot.peek()[0].neutral, 2.0)
    o = pipe.out_slot.peek()[0]
    assert o.image.shape == (360, 640, 3) and int(o.image.max()) < 40   # flat neutral frame
    assert pipe.tracker.state == TrackState.SOURCE_UNAVAILABLE


def test_tracking_loss_grace_then_panel(pipe):
    pipe.tracker.cfg.grace_s = 0.5
    pipe.tracker.cfg.lost_after_s = 0.2
    src = FakeSource()
    pipe.set_source(src)
    pipe.set_panel(PANEL)
    assert wait_for(lambda: pipe.tracker.single_clear_candidate() is not None)
    pipe.select_detection(pipe.tracker.candidates[0].box)
    assert wait_for(lambda: pipe.tracker.state == TrackState.TRACKING)
    pipe.detector.extra = [Rect(250, 100, 40, 50)]   # a second face appears far away
    pipe.detector.enabled = False                   # ...and the selected one vanishes
    pipe.detector.extra = []
    assert wait_for(lambda: pipe.tracker.state == TrackState.LOST, 2.0)
    d = pipe.tracker.decision(time.perf_counter())
    assert d.mode == "hold"
    time.sleep(0.6)
    assert pipe.tracker.decision(time.perf_counter()).mode == "panel"
    pipe.detector.enabled = True
    assert wait_for(lambda: pipe.tracker.state == TrackState.TRACKING, 2.0)


def test_does_not_jump_to_second_face(pipe):
    pipe.tracker.cfg.lost_after_s = 0.2
    src = FakeSource()
    pipe.set_source(src)
    pipe.set_panel(PANEL)
    assert wait_for(lambda: pipe.tracker.single_clear_candidate() is not None)
    pipe.select_detection(pipe.tracker.candidates[0].box)
    pipe.detector.enabled = False
    pipe.detector.extra = [Rect(260, 20, 40, 50)]  # distractor only
    time.sleep(1.0)
    assert pipe.tracker.state == TrackState.LOST
    assert pipe.tracker.target.cx < 120


def test_panel_invalid_gives_neutral(pipe):
    src = FakeSource()
    pipe.set_source(src)
    pipe.set_panel(PANEL)
    assert wait_for(lambda: pipe.out_slot.peek()[0] is not None and not pipe.out_slot.peek()[0].neutral)
    from faceframe.core.panel_validate import PanelValidity
    with pipe._lock:
        pipe.panel_validity = PanelValidity.INVALID
    assert wait_for(lambda: pipe.out_slot.peek()[0].neutral, 1.0)


def test_core_imports_without_qt():
    import subprocess
    import sys
    code = ("import sys; import faceframe.core.pipeline, faceframe.capture.wgc, faceframe.capture.bridge, "
            "faceframe.settings; bad=[m for m in sys.modules if m.startswith('PySide6')]; print(bad); "
            "sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode == 0, r.stdout + r.stderr
