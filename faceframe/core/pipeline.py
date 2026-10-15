"""Capture -> detection/tracking -> crop -> enhancement -> output, GUI-free.

Threads
-------
source thread(s)   deliver owned Frames / Overviews (WGC callback, bridge socket)
detect thread      latest frame at ~10-15 Hz (adaptive) -> DetectionResult -> tracker
process thread     newest frame at the output rate -> smoothed crop -> render -> out_slot
validate thread    overviews at ~1-2 Hz -> panel geometry validation / relocation
UI (not here)      polls out_slot; the output window paints only new frames

Every hand-off is a LatestSlot (capacity 1): obsolete work is dropped, never
queued. Detection results carry their frame's session/seq/timestamp and are
rejected if stale. Changing source flushes slots and resets tracker state.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .detector import YuNetDetector
from .enhance import QualityMode, QualitySettings, RenderInfo, Renderer, neutral_frame
from .frame import ClockDomain, Frame, Overview, now_ns
from .geometry import (Rect, fit_crop_to_panel, snap_crop_to_aspect, snap_crop_to_pixels,
                       target_crop_for_face)
from .metrics import Ema, LatencyStats, RateCounter
from .panel_validate import PanelValidator, PanelValidity
from .queues import LatestSlot
from .source_base import AVAILABLE_STATES, SourceState, SourceStatus
from .temporal import TemporalFuser
from .tracking import (CropSmoother, DetectionResult, FaceTracker, SmootherParams, TrackerConfig,
                       TrackState)

log = logging.getLogger("faceframe.pipeline")


@dataclass
class PipelineConfig:
    out_w: int = 1280
    out_h: int = 720
    out_fps: int = 30
    framing: str = "head_shoulders"
    fill: bool = True
    auto_frame: bool = True
    lock: bool = False
    smoothness: float = 0.55
    detect_hz: float = 12.0
    unavailable_output: str = "neutral"   # neutral | hold
    quality: QualitySettings = field(default_factory=QualitySettings)
    compare: bool = False                 # produce same-frame comparison images
    compare_mode: QualityMode = QualityMode.FAST
    temporal: bool = False                # motion-compensated multi-frame fusion of the panel
    temporal_strength: float = 0.75


@dataclass
class OutputFrame:
    image: np.ndarray            # BGR, exactly out_w x out_h
    seq: int                     # output sequence (unique processed frames)
    src_seq: int
    session_id: int
    t_capture_ns: int
    clock: ClockDomain
    t_done_ns: int
    crop: Rect | None            # in panel pixels
    panel_size: tuple[int, int]
    info: RenderInfo | None
    state: str
    neutral: bool = False


@dataclass
class CompareFrame:
    a: np.ndarray
    b: np.ndarray
    label_a: str
    label_b: str
    src_seq: int
    crop_size: tuple[int, int]


def _panel_crop(panel_w: float, panel_h: float, aspect: float, fill: bool) -> Rect:
    full = Rect(0, 0, panel_w, panel_h)
    if not fill:
        return full
    if panel_w / panel_h > aspect:
        w = panel_h * aspect
        return Rect((panel_w - w) / 2, 0, w, panel_h)
    h = panel_w / aspect
    return Rect(0, (panel_h - h) / 2, panel_w, h)


class Pipeline:
    def __init__(self, models_dir: str | Path, cfg: PipelineConfig | None = None, detector=None) -> None:
        self.models_dir = Path(models_dir)
        self.cfg = cfg or PipelineConfig()
        self.detector = detector or YuNetDetector(self.models_dir / "face_detection_yunet_2023mar.onnx")
        self.tracker = FaceTracker(TrackerConfig())
        self.smoother = CropSmoother(SmootherParams(smoothness=self.cfg.smoothness))
        self.renderer = Renderer()
        self.validator = PanelValidator()
        self.fuser = TemporalFuser(self.cfg.temporal_strength)
        self._lock = threading.RLock()

        self.frame_slot: LatestSlot[Frame] = LatestSlot("process")
        self.det_slot: LatestSlot[Frame] = LatestSlot("detect")
        self.ov_slot: LatestSlot[Overview] = LatestSlot("validate")
        self.out_slot: LatestSlot[OutputFrame] = LatestSlot("output")
        self.compare_slot: LatestSlot[CompareFrame] = LatestSlot("compare")
        self.latest_overview: Overview | None = None
        self.latest_frame: Frame | None = None

        self.source = None
        self.session_id = 0
        self.source_status = SourceStatus(SourceState.IDLE, "No source selected")
        self.panel: Rect | None = None              # in source pixels
        self.panel_validity = PanelValidity.CHECKING
        self.panel_score = float("nan")
        self.panel_event = ""                       # last validation event for the UI
        self._last_relocate_t = 0.0
        self.require_confirmed_panel = False
        self._geom_changed_t = 0.0          # perf time of the last source geometry change
        self.geometry_settle_s = 1.0
        self._suspect_since = 0.0           # validator saw a mismatch; confirming a move/change
        self.suspect_hold_s = 2.0

        # metrics
        self.cap_rate = RateCounter()
        self.unique_rate = RateCounter()
        self._last_sig = None
        self.proc_rate = RateCounter()
        self.det_rate = RateCounter()
        self.proc_latency = LatencyStats()      # capture timestamp -> processed (same clock domain)
        self.queue_age = LatencyStats()         # capture timestamp -> processing start
        self.det_ms = Ema(0.2)
        self.proc_ms = Ema(0.2)
        self.fuse_ms = Ema(0.2)
        self.frames_in = 0
        self.frames_stale_session = 0
        self.frames_superseded = 0
        self._out_seq = 0
        self._last_out: OutputFrame | None = None
        self._last_frame_t_ns: int | None = None
        self._last_compare_t = 0.0
        self._effective_det_hz = self.cfg.detect_hz

        self._stop = threading.Event()
        self._threads = [
            threading.Thread(target=self._detect_loop, name="ff-detect", daemon=True),
            threading.Thread(target=self._process_loop, name="ff-process", daemon=True),
            threading.Thread(target=self._validate_loop, name="ff-validate", daemon=True),
        ]
        for t in self._threads:
            t.start()

    # ================================================================ control
    def set_source(self, source) -> None:
        """Bind a new source. Flushes all queued work and tracker state."""
        self.stop_source()
        with self._lock:
            self.session_id += 1
            sid = self.session_id
            self.source = source
            source.session_id = sid
            self._flush(sid)
            self.panel = None
            self.panel_validity = PanelValidity.CHECKING
            self.validator.reset()
        source.start(self)

    def stop_source(self) -> None:
        with self._lock:
            src, self.source = self.source, None
            self.session_id += 1
            self._flush(self.session_id)
        if src is not None:
            try:
                src.stop()
            except Exception:
                log.exception("source stop failed")
        self.source_status = SourceStatus(SourceState.STOPPED, "Source stopped")
        self.tracker.set_source_available(False, time.perf_counter())

    def _flush(self, sid: int) -> None:
        for slot in (self.frame_slot, self.det_slot, self.ov_slot, self.compare_slot):
            slot.clear()
        self.tracker.reset(sid)
        self.smoother = CropSmoother(SmootherParams(smoothness=self.cfg.smoothness))
        self.latest_frame = None
        self._last_frame_t_ns = None
        self.fuser.reset()

    def set_panel(self, panel: Rect | None, reference: tuple | None = None) -> None:
        """Panel in source pixels (None clears). Resets person selection.

        ``reference`` = (gray_image, overview_scale, source_size) from a previous
        session: output stays neutral until the live content matches it."""
        with self._lock:
            self.panel = panel
            self.panel_validity = PanelValidity.CHECKING
            self.validator.reset()
            self.require_confirmed_panel = reference is not None
            self._geom_changed_t = 0.0
            self._suspect_since = 0.0
            if reference is not None:
                self.validator.preload_reference(*reference)
            self.session_id += 1           # detections for the old panel become stale
            sid = self.session_id
            if self.source is not None:
                self.source.session_id = sid
                self.source.set_panel(panel)
                self.source.request_overview()
            self._flush(sid)
            self.tracker.set_source_available(self._source_available(), time.perf_counter())

    def select_person_at(self, px: float, py: float) -> bool:
        with self._lock:
            d = self.tracker.select_at(px, py, time.perf_counter())
            if d is not None:
                self.smoother = CropSmoother(SmootherParams(smoothness=self.cfg.smoothness))
            return d is not None

    def select_detection(self, box: Rect) -> None:
        with self._lock:
            self.tracker.select_box(box, time.perf_counter())

    def reset_tracking(self) -> None:
        with self._lock:
            self.tracker.reset()
            self.smoother = CropSmoother(SmootherParams(smoothness=self.cfg.smoothness))

    def update_config(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self.cfg, k, v)
            if "smoothness" in kw:
                self.smoother.params.smoothness = self.cfg.smoothness
            if "detect_hz" in kw:
                self._effective_det_hz = self.cfg.detect_hz

    def set_sr_engine(self, engine) -> None:
        self.renderer.set_engine(engine)

    def shutdown(self) -> None:
        self.stop_source()
        self._stop.set()
        for slot in (self.frame_slot, self.det_slot, self.ov_slot):
            slot.close()
        for t in self._threads:
            t.join(timeout=3)
        if self.renderer.sr is not None:
            try:
                self.renderer.sr.close()
            except Exception:
                pass

    def _source_available(self) -> bool:
        return self.source is not None and self.source_status.state in AVAILABLE_STATES

    # ============================================================ sink (source threads)
    def on_frame(self, frame: Frame) -> None:
        if frame.session_id != self.session_id:
            self.frames_stale_session += 1
            return
        self.frames_in += 1
        self.cap_rate.tick()
        # unique-content counter: WGC may deliver repeated compositions of the
        # same video frame; count distinct panel images separately.
        sig = hash(frame.pixels[::7, ::7].tobytes())
        if sig != self._last_sig:
            self._last_sig = sig
            self.unique_rate.tick()
        self.latest_frame = frame
        self.frame_slot.put(frame)
        self.det_slot.put(frame)

    def on_overview(self, ov: Overview) -> None:
        # Overviews are panel-independent: accept any from the current source.
        src = self.source
        if src is None or ov.source_id != src.source_id:
            return
        self.latest_overview = ov
        self.ov_slot.put(ov)

    def on_status(self, st: SourceStatus) -> None:
        prev = self.source_status.state
        self.source_status = st
        now = time.perf_counter()
        if st.state == SourceState.GEOMETRY_CHANGED:
            with self._lock:
                newp = st.details.get("panel")
                if isinstance(newp, Rect) and self.panel is not None:
                    # native rendition switch: same layout, uniformly rescaled panel
                    self._apply_panel_move(self.panel, newp, True, float("nan"))
                    self.validator.reset()  # reference re-taken at the new rendition
                self.panel_validity = PanelValidity.CHECKING
                self.panel_event = st.message
                self._geom_changed_t = time.perf_counter()
            return
        avail = st.state in AVAILABLE_STATES
        with self._lock:
            self.tracker.set_source_available(avail, now)
        if prev != st.state:
            log.info("source %s: %s %s", st.state.value, st.message, st.recovery)

    def on_panel_selected_in_page(self, rect: Rect) -> None:
        """Native bridge: the user dragged the webcam area on the page itself."""
        if rect.w >= 8 and rect.h >= 8:
            self.set_panel(rect)
            self.panel_event = f"Webcam area selected in the page: {rect.to_int()[2]}x{rect.to_int()[3]} video px"

    # ================================================================ detect
    def _detect_loop(self) -> None:
        seen = 0
        last_t = 0.0
        while not self._stop.is_set():
            frame, seen = self.det_slot.get_newer(seen, timeout=0.2)
            if frame is None:
                with self._lock:
                    self.tracker.tick(time.perf_counter())
                continue
            period = 1.0 / max(1.0, self._effective_det_hz)
            wait = period - (time.perf_counter() - last_t)
            if wait > 0:
                time.sleep(wait)
                newer, v = self.det_slot.get_newer(seen, timeout=0)
                if newer is not None:
                    frame, seen = newer, v
            if frame.session_id != self.session_id:
                continue
            last_t = time.perf_counter()
            try:
                dets = self.detector.detect(frame.pixels)
            except Exception:
                log.exception("detector failed")
                continue
            ms = self.detector.last_ms
            self.det_ms.add(ms)
            self.det_rate.tick()
            # adapt: keep detection under ~40% of one core-period
            target = self.cfg.detect_hz
            if ms > 0.4 * 1000.0 / target:
                self._effective_det_hz = max(5.0, 400.0 / ms)
            else:
                self._effective_det_hz = target
            res = DetectionResult(frame.session_id, frame.seq, frame.t_capture_ns, frame.size, dets, ms)
            with self._lock:
                self.tracker.update(res, time.perf_counter())

    # ================================================================ process
    def _process_loop(self) -> None:
        seen = 0
        next_due = 0.0
        while not self._stop.is_set():
            frame, seen = self.frame_slot.get_newer(seen, timeout=0.1)
            now = time.perf_counter()
            if frame is None:
                self._idle_output()
                continue
            period = 1.0 / max(1, self.cfg.out_fps)
            if now < next_due:
                # Too early for the next output frame: wait, then take the newest.
                time.sleep(next_due - now)
                newer, v = self.frame_slot.get_newer(seen, timeout=0)
                if newer is not None:
                    frame, seen = newer, v
                    self.frames_superseded += 1   # taken but replaced by a newer frame
                now = time.perf_counter()
            # keep a steady cadence; resynchronise after a gap instead of bursting
            next_due = next_due + period if now - next_due < period else now + period
            if frame.session_id != self.session_id:
                continue
            try:
                self._process_frame(frame)
            except Exception:
                log.exception("processing failed")

    def _idle_output(self) -> None:
        """No frames: if the source is unavailable / panel invalid, publish a neutral frame once."""
        if self._should_show_neutral() and (self._last_out is None or not self._last_out.neutral):
            if self.cfg.unavailable_output == "neutral" or self._last_out is None:
                self._publish_neutral()

    def _should_show_neutral(self) -> bool:
        return (not self._source_available()) or self.panel is None \
            or self.panel_validity == PanelValidity.INVALID \
            or (self.require_confirmed_panel and self.panel_validity != PanelValidity.VALID) \
            or self._geometry_pending()

    def _geometry_pending(self) -> bool:
        """Source geometry changed and the panel has not been re-validated yet:
        the stored rectangle may point at unrelated pixels, so do not frame it."""
        return self._geom_changed_t > 0 and self.panel_validity != PanelValidity.VALID

    def confirm_panel(self) -> None:
        """User confirmed a restored panel by eye."""
        with self._lock:
            self.require_confirmed_panel = False
            if self.latest_overview is not None and self.panel is not None:
                ov = self.latest_overview
                self.validator.set_reference(ov.pixels, ov.scale, self.panel, ov.source_size)
            self.panel_validity = PanelValidity.VALID
            self._geom_changed_t = 0.0

    def _publish_neutral(self, frame: Frame | None = None) -> None:
        cfg = self.cfg
        self._out_seq += 1
        t = now_ns()
        out = OutputFrame(neutral_frame(cfg.out_w, cfg.out_h), self._out_seq, frame.seq if frame else -1,
                          self.session_id, frame.t_capture_ns if frame else t,
                          frame.clock if frame else ClockDomain.HOST_PERF, t, None, (0, 0), None,
                          self.tracker.state.value, neutral=True)
        self._last_out = out
        self.out_slot.put(out)

    def _process_frame(self, frame: Frame) -> None:
        t_start = now_ns()
        cfg = self.cfg
        self.queue_age.add((t_start - frame.t_capture_ns) / 1e6)
        pw, ph = frame.size
        aspect = cfg.out_w / cfg.out_h
        now_s = time.perf_counter()
        with self._lock:
            invalid = self.panel_validity == PanelValidity.INVALID or (
                self.require_confirmed_panel and self.panel_validity != PanelValidity.VALID) or (
                self._geometry_pending())
            dec = self.tracker.decision(now_s)
            dt = 0.0 if self._last_frame_t_ns is None else (frame.t_capture_ns - self._last_frame_t_ns) / 1e9
            self._last_frame_t_ns = frame.t_capture_ns
            if self._suspect_since:
                # content at the panel no longer matches (scroll/layout move being confirmed):
                # freeze the last good output briefly instead of framing unrelated pixels
                if time.perf_counter() - self._suspect_since < self.suspect_hold_s and self._last_out is not None:
                    return
                invalid = True
            if invalid or dec.mode == "neutral":
                # publish the neutral frame once; repeating it would look like new video
                if (cfg.unavailable_output == "neutral" or self._last_out is None) and                         not (self._last_out is not None and self._last_out.neutral):
                    self._publish_neutral(frame)
                return
            if not cfg.auto_frame:
                target = _panel_crop(pw, ph, aspect, cfg.fill)
            elif cfg.lock and self.smoother.initialized:
                target = self.smoother.current()
            elif dec.mode in ("face", "hold") and dec.face is not None:
                target = target_crop_for_face(dec.face, aspect, cfg.framing, pw, ph, cfg.fill)
            else:
                target = _panel_crop(pw, ph, aspect, cfg.fill)
            if cfg.lock and self.smoother.initialized:
                crop = self.smoother.current()
            else:
                crop = self.smoother.update(target, dt)
            crop = fit_crop_to_panel(crop, pw, ph, cfg.fill)
            state = dec.state.value
        if cfg.fill:
            x, y, w, h = snap_crop_to_aspect(crop, aspect, pw, ph)
        else:
            x, y, w, h = snap_crop_to_pixels(crop, pw, ph)
        panel_px = frame.pixels
        if cfg.temporal:
            t_f = time.perf_counter()
            self.fuser.strength = cfg.temporal_strength
            panel_px = self.fuser.process(frame.pixels)   # whole panel, before cropping
            self.fuse_ms.add((time.perf_counter() - t_f) * 1000.0)
        crop_px = panel_px[y:y + h, x:x + w]
        img, info = self.renderer.render(crop_px, cfg.out_w, cfg.out_h, cfg.quality,
                                         probe_stability=cfg.quality.mode == QualityMode.ENHANCED,
                                         fill=cfg.fill)
        t_done = now_ns()
        self._out_seq += 1
        out = OutputFrame(img, self._out_seq, frame.seq, frame.session_id, frame.t_capture_ns, frame.clock,
                          t_done, Rect(x, y, w, h), (pw, ph), info, state)
        self._last_out = out
        self.out_slot.put(out)
        self.proc_rate.tick()
        self.proc_ms.add((t_done - t_start) / 1e6)
        self.proc_latency.add((t_done - frame.t_capture_ns) / 1e6)
        if cfg.compare and time.perf_counter() - self._last_compare_t > 0.066:
            self._last_compare_t = time.perf_counter()
            alt_q = QualitySettings(**{**cfg.quality.__dict__, "mode": cfg.compare_mode, "auto_fallback": False})
            alt, alt_info = self.renderer.render(crop_px, cfg.out_w, cfg.out_h, alt_q, fill=cfg.fill)
            self.compare_slot.put(CompareFrame(img, alt, f"{info.mode_used.value} (live)",
                                               f"{alt_info.mode_used.value}", frame.seq, (w, h)))

    # =============================================================== validate
    def _validate_loop(self) -> None:
        seen = 0
        while not self._stop.is_set():
            ov, seen = self.ov_slot.get_newer(seen, timeout=0.3)
            if ov is None:
                continue
            with self._lock:
                panel = self.panel
                src = self.source
            if panel is None or src is None or ov.source_id != src.source_id:
                continue
            # let the page re-layout after a resize before trusting any overview
            if self._geom_changed_t and (ov.t_ns / 1e9) < self._geom_changed_t + self.geometry_settle_s:
                continue
            relocating = self.validator.ref_source_size not in (None, ov.source_size)
            if relocating and time.perf_counter() - self._last_relocate_t < 1.5:
                continue
            if relocating:
                self._last_relocate_t = time.perf_counter()
            try:
                state, new_panel, score = self.validator.check(ov.pixels, ov.scale, panel, ov.source_size)
            except Exception:
                log.exception("panel validation failed")
                continue
            with self._lock:
                if self.panel is not panel:
                    continue  # user changed the panel meanwhile
                if state == PanelValidity.CHECKING and score < self.validator.cfg.invalid_score:
                    self._suspect_since = self._suspect_since or time.perf_counter()
                else:
                    self._suspect_since = 0.0
                prev = self.panel_validity
                self.panel_validity = state
                self.panel_score = score
                if new_panel != panel and state == PanelValidity.VALID:
                    self._apply_panel_move(panel, new_panel, relocating, score)
                if state == PanelValidity.VALID:
                    self.require_confirmed_panel = False
                    self._geom_changed_t = 0.0
                if prev != state:
                    msg = {PanelValidity.VALID: "Webcam area confirmed",
                           PanelValidity.INVALID: "Webcam area no longer matches (layout/scene changed): "
                                                  "showing neutral output — reselect the webcam area, or wait "
                                                  "if this is an ad/break",
                           PanelValidity.CHECKING: "Checking webcam area…"}[state]
                    self.panel_event = f"{msg} (score {score:.2f})"
                    log.info(self.panel_event)

    def _apply_panel_move(self, old: Rect, new: Rect, relocated: bool, score: float) -> None:
        sx, sy = new.w / old.w, new.h / old.h
        self.panel = new
        if self.source is not None:
            self.source.set_panel(new)
        if abs(sx - 1) > 1e-3 or abs(sy - 1) > 1e-3:
            # panel content was rescaled: rescale tracker/smoother state in panel coords
            t = self.tracker.target
            if t is not None:
                self.tracker.target = t.scaled(sx, sy)
            sm = self.smoother
            if sm.initialized:
                sm.cx *= sx; sm.cy *= sy; sm.w *= sx; sm.h *= sy  # noqa: E702
            self.tracker.candidates = []
        self.panel_event = (f"Webcam area {'relocated after resize' if relocated else 'followed scroll'} "
                            f"(score {score:.2f})")
        log.info(self.panel_event)

    # ================================================================= status
    def status(self) -> dict:
        p = self.proc_latency.percentiles()
        qa = self.queue_age.percentiles()
        lf = self.latest_frame
        out = self._last_out
        info = out.info if out else None
        return {
            "source_state": self.source_status.state.value,
            "source_msg": self.source_status.message,
            "source_recovery": self.source_status.recovery,
            "track_state": self.tracker.state.value,
            "panel_validity": self.panel_validity.value,
            "panel_score": self.panel_score,
            "panel_event": self.panel_event,
            "candidates": len(self.tracker.candidates),
            "capture_fps": self.cap_rate.rate(),
            "unique_source_fps": self.unique_rate.rate(),
            "process_fps": self.proc_rate.rate(),
            "detect_hz": self.det_rate.rate(),
            "detect_ms": self.det_ms.value or 0.0,
            "process_ms": self.proc_ms.value or 0.0,
            "latency_p50": p[50], "latency_p95": p[95], "latency_p99": p[99],
            "queue_age_p50": qa[50], "queue_age_p95": qa[95],
            "latency_clock": lf.clock.value if lf else "",
            "dropped_process": self.frame_slot.dropped + self.frames_superseded,
            "dropped_detect": self.det_slot.dropped,
            "stale_detections": self.tracker.stale_rejected,
            "panel_px": lf.size if lf else None,
            "source_size": lf.source_size if lf else None,
            "video_size": lf.video_size if lf else None,
            "source_kind": lf.source_kind.value if lf else "",
            "crop_px": (info.crop_size if info else None),
            "out_px": (self.cfg.out_w, self.cfg.out_h),
            "drawn_px": (info.drawn_size if info else None),
            "scale": (info.scale if info else None),
            "mode_used": (info.mode_used.value if info else ""),
            "mode_reason": (info.reason if info else ""),
            "warning": (info.warning if info else ""),
            "sr_ms": (self.renderer.sr_ms.value or 0.0),
            "flicker_ratio": self.renderer.flicker_ratio.value,
            "fallback": self.renderer.fallback_reason,
            "fuse_ms": (self.fuse_ms.value or 0.0) if self.cfg.temporal else 0.0,
        }
