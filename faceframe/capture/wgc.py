"""Windows Graphics Capture source bound to ONE selected window handle.

Pixels are the DWM-composited *rendered window* (Edge chrome + page), never
the Twitch decoder output. The capture is bound by HWND (never by title
match) and never falls back to another window or the desktop.

Buffer handling: windows-capture hands us a zero-copy BGRA view (row pitch may
exceed width*4) that is only valid inside the callback. We copy just the panel
(and, at a low rate, a downscaled overview) into owned arrays before returning.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import cv2
import numpy as np

from ..core.frame import ClockDomain, Frame, Overview, SourceKind, now_ns
from ..core.geometry import Rect, snap_crop_to_pixels
from ..core.source_base import SourceSink, SourceState, SourceStatus
from . import windows as win

log = logging.getLogger("faceframe.wgc")

OVERVIEW_LONG_SIDE = 960


class SourceRefused(RuntimeError):
    pass


def validate_target(hwnd: int, allow_any_exe: bool = False) -> win.WindowInfo:
    if not win.is_window(hwnd):
        raise SourceRefused("The selected window no longer exists.")
    wi = win.info(hwnd)
    if wi.pid == os.getpid():
        raise SourceRefused("Refusing to capture FaceFrame's own window (feedback loop).")
    if not allow_any_exe and wi.exe.lower() != "msedge.exe":
        raise SourceRefused(f"Selected window belongs to {wi.exe or 'unknown'}, not Microsoft Edge.")
    return wi


def grab_preview(hwnd: int, timeout_s: float = 1.5, long_side: int = 360) -> np.ndarray | None:
    """One identifying preview frame (BGR, downscaled) or None if nothing arrives
    (e.g. minimized window)."""
    from windows_capture import WindowsCapture

    if win.is_minimized(hwnd):
        return None
    result: dict = {}
    done = threading.Event()
    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=int(hwnd))

    @cap.event
    def on_frame_arrived(frame, control):
        if "img" not in result:
            buf = frame.frame_buffer
            h, w = buf.shape[:2]
            s = long_side / max(w, h)
            small = cv2.resize(buf, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
            result["img"] = cv2.cvtColor(small, cv2.COLOR_BGRA2BGR)
            result["size"] = (w, h)
            done.set()
        control.stop()

    @cap.event
    def on_closed():
        done.set()

    try:
        ctl = cap.start_free_threaded()
    except Exception as e:
        log.warning("preview capture failed for %#x: %s", hwnd, e)
        return None
    done.wait(timeout_s)
    try:
        ctl.stop()
        ctl.wait()
    except Exception:
        pass
    return result.get("img")


class WgcSource:
    """WGC window source. Thread model: frames arrive on the windows-capture
    thread; a watchdog thread reports stalls/minimize/close; nothing blocks."""

    kind = SourceKind.WGC_RENDERED

    def __init__(self, hwnd: int, session_id: int, max_fps: int = 60, overview_hz: float = 2.0,
                 stall_s: float = 1.5, allow_any_exe: bool = False) -> None:
        self.hwnd = int(hwnd)
        self.session_id = session_id
        self.max_fps = max_fps
        self.overview_hz = overview_hz
        self.stall_s = stall_s
        self.allow_any_exe = allow_any_exe
        self.window = validate_target(self.hwnd, allow_any_exe)
        self.source_id = f"wgc:{self.hwnd:#x}:{self.window.pid}"
        self._sink: SourceSink | None = None
        self._ctl = None
        self._panel: Rect | None = None
        self._panel_lock = threading.Lock()
        self._seq = 0
        self._last_frame_t = 0.0
        self._last_ov_t = 0.0
        self._ov_requested = True
        self._size: tuple[int, int] | None = None
        self._stop = threading.Event()
        self._stopping = False
        self._watchdog: threading.Thread | None = None
        self._state = SourceState.IDLE
        self._restarts = 0
        self.frames_in = 0
        self.callback_ms = 0.0

    # --------------------------------------------------------------- control
    def describe(self) -> dict:
        return {"kind": self.kind.value, "hwnd": self.hwnd, "title": self.window.title,
                "pid": self.window.pid, "exe": self.window.exe, "dpi": self.window.dpi,
                "window_px": self.window.rect[2:], "capture_size": self._size,
                "note": "Rendered window pixels (DWM composition), not decoder pixels."}

    def set_panel(self, panel: Rect | None) -> None:
        with self._panel_lock:
            self._panel = panel

    def request_overview(self) -> None:
        self._ov_requested = True

    def set_overview_rate(self, hz: float) -> None:
        """Higher while the Source preview is visible (smooth), low otherwise (validation only)."""
        self.overview_hz = max(0.5, float(hz))

    def start(self, sink: SourceSink) -> None:
        self._sink = sink
        self._stop.clear()
        self._stopping = False
        self._set_state(SourceState.STARTING, "Starting window capture")
        self._start_capture()
        self._watchdog = threading.Thread(target=self._watch, name="wgc-watchdog", daemon=True)
        self._watchdog.start()

    def _start_capture(self) -> None:
        from windows_capture import WindowsCapture

        validate_target(self.hwnd, self.allow_any_exe)
        cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=self.hwnd,
                             minimum_update_interval=max(1, int(1000 / self.max_fps)))
        cap.event(self._make_on_frame())
        cap.event(self._make_on_closed())
        self._ctl = cap.start_free_threaded()

    def stop(self) -> None:
        self._stopping = True
        self._stop.set()
        ctl, self._ctl = self._ctl, None
        if ctl is not None:
            try:
                ctl.stop()
                ctl.wait()
            except Exception as e:
                log.debug("stop: %s", e)
        if self._watchdog is not None:
            self._watchdog.join(timeout=2.0)
        self._set_state(SourceState.STOPPED, "Capture stopped; resources released")

    # -------------------------------------------------------------- callbacks
    def _make_on_frame(self):
        def on_frame_arrived(frame, control):
            if self._stop.is_set():
                control.stop()
                return
            t_ns = now_ns()
            t0 = time.perf_counter()
            buf = frame.frame_buffer  # (H, W, 4) BGRA view, valid only in this call
            h, w = buf.shape[:2]
            size = (w, h)
            if self._size is not None and size != self._size:
                self._set_state(SourceState.GEOMETRY_CHANGED,
                                f"Window size changed {self._size[0]}x{self._size[1]} -> {w}x{h}",
                                "Re-validating the webcam area…", {"old": self._size, "new": size})
                self._set_state(SourceState.RUNNING, f"Capturing ({w}x{h} after resize)")
                self._ov_requested = True
            self._size = size
            self._last_frame_t = time.monotonic()
            self.frames_in += 1
            if self._state != SourceState.RUNNING:
                self._set_state(SourceState.RUNNING, "Capturing")
            sink = self._sink
            if sink is None:
                return
            now = time.monotonic()
            if self._ov_requested or now - self._last_ov_t >= 1.0 / self.overview_hz:
                self._ov_requested = False
                self._last_ov_t = now
                s = min(1.0, OVERVIEW_LONG_SIDE / max(w, h))
                small = cv2.resize(buf, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
                sink.on_overview(Overview(self.source_id, self.session_id, t_ns, size,
                                          cv2.cvtColor(small, cv2.COLOR_BGRA2BGR), s, self.kind))
            with self._panel_lock:
                panel = self._panel
            if panel is not None:
                x, y, pw, ph = snap_crop_to_pixels(panel, w, h)
                pixels = cv2.cvtColor(buf[y:y + ph, x:x + pw], cv2.COLOR_BGRA2BGR)  # owned copy
                self._seq += 1
                sink.on_frame(Frame(self.source_id, self.session_id, self._seq, t_ns, ClockDomain.HOST_PERF,
                                    t_ns, self.kind, size, Rect(x, y, pw, ph), pixels))
            self.callback_ms = (time.perf_counter() - t0) * 1000.0

        return on_frame_arrived

    def _make_on_closed(self):
        def on_closed():
            if self._stopping:
                return
            if not win.is_window(self.hwnd):
                self._set_state(SourceState.CLOSED, "Source window was closed",
                                "Open Twitch in Edge again and use Select Source")
            else:
                self._set_state(SourceState.FAILED, "Capture session ended unexpectedly",
                                "Retrying on the same window…")
        return on_closed

    # --------------------------------------------------------------- watchdog
    def _watch(self) -> None:
        while not self._stop.wait(0.5):
            if not win.is_window(self.hwnd):
                if self._state != SourceState.CLOSED:
                    self._set_state(SourceState.CLOSED, "Source window was closed",
                                    "Open Twitch in Edge again and use Select Source")
                    self._shutdown_capture()
                continue
            if self._state == SourceState.CLOSED:
                continue
            age = time.monotonic() - self._last_frame_t if self._last_frame_t else None
            if win.is_minimized(self.hwnd):
                if self._state != SourceState.MINIMIZED:
                    self._set_state(SourceState.MINIMIZED, "Edge window is minimized: no frames are rendered",
                                    "Restore the Edge window (it may be covered, but not minimized)")
            elif self._state == SourceState.FAILED or (self._ctl is not None and self._ctl.is_finished()):
                # Capture thread died (e.g. device lost): restart on the SAME hwnd only.
                if self._restarts < 20:
                    self._restarts += 1
                    log.warning("WGC session ended; restarting on hwnd %#x (attempt %d)", self.hwnd, self._restarts)
                    self._shutdown_capture()
                    try:
                        self._start_capture()
                        self._set_state(SourceState.STARTING, "Capture restarted after failure")
                    except Exception as e:
                        self._set_state(SourceState.FAILED, f"Capture restart failed: {e}",
                                        "Use Select Source to pick the window again")
                        time.sleep(1.0)
            elif age is not None and age > self.stall_s:
                if self._state not in (SourceState.STALLED,):
                    self._set_state(SourceState.STALLED, f"No new frames for {age:.1f}s",
                                    "Is the stream paused? Keep the Edge window restored (it may be covered)")
            elif age is None and self._state == SourceState.STARTING:
                pass

    def _shutdown_capture(self) -> None:
        ctl, self._ctl = self._ctl, None
        if ctl is not None:
            try:
                ctl.stop()
                ctl.wait()
            except Exception:
                pass

    def _set_state(self, st: SourceState, msg: str = "", recovery: str = "", details: dict | None = None) -> None:
        self._state = st
        if self._sink is not None:
            try:
                self._sink.on_status(SourceStatus(st, msg, recovery, details or {}))
            except Exception:  # sink errors must not kill capture
                log.exception("status sink failed")

    @property
    def state(self) -> SourceState:
        return self._state
