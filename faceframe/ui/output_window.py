"""The clean "FaceFrame Output" window that LIVE Studio captures.

* Contains only the processed picture (no widgets, overlays or text).
* Client area is sized in PHYSICAL pixels: logical size = physical / DPR, the
  image is drawn 1:1 in device pixels, and the real client rect is verified
  with GetClientRect after every show/resize/DPI change.
* Paints only when a new output frame exists; repeats are counted, not faked.
* Independent of the control window: hiding the control window keeps it alive.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

from ..core.frame import ClockDomain, now_ns
from ..core.metrics import LatencyStats, RateCounter
from .qimage import bgr_to_qimage_view

OUTPUT_TITLE = "FaceFrame Output"

_user32 = ctypes.WinDLL("user32")
_user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.IsIconic.argtypes = [wintypes.HWND]


class OutputWindow(QWidget):
    sizeVerified = Signal(int, int, bool)   # physical w, h, matches

    def __init__(self, pipeline, width: int, height: int, frameless: bool = True) -> None:
        super().__init__(None)
        self.pipeline = pipeline
        self.setWindowTitle(OUTPUT_TITLE)
        from .theme import app_icon
        self.setWindowIcon(app_icon())
        flags = Qt.WindowType.Window
        if frameless:
            flags |= Qt.WindowType.FramelessWindowHint
        else:
            flags |= Qt.WindowType.WindowTitleHint | Qt.WindowType.CustomizeWindowHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._phys = (int(width), int(height))
        self._img = None
        self._arr: np.ndarray | None = None
        self._seen = 0
        self._painted_seq = -1
        self._pending_capture_ns: int | None = None
        self._pending_clock: ClockDomain | None = None
        self._drag: QPoint | None = None
        self.unique_rate = RateCounter()
        self.tick_rate = RateCounter()
        self.repeats = 0
        self.uniques = 0
        self.paint_latency = LatencyStats()   # capture ts -> paint completed (host domain only)
        self.client_px = (0, 0)
        self.size_ok = False
        self.minimized = False
        self.paused = False
        self.test_pattern = False
        self._pattern_arr: np.ndarray | None = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._poll)
        self._apply_size()

    # ---------------------------------------------------------------- sizing
    def set_output_size(self, width: int, height: int) -> None:
        self._phys = (int(width), int(height))
        self._apply_size()

    def _apply_size(self) -> None:
        dpr = self.devicePixelRatioF() if self.windowHandle() else self.screen().devicePixelRatio()
        lw = self._phys[0] / dpr
        lh = self._phys[1] / dpr
        self.setFixedSize(round(lw), round(lh))
        QTimer.singleShot(50, self.verify_size)

    def verify_size(self) -> tuple[int, int, bool]:
        if not self.isVisible():
            return (0, 0, False)
        r = wintypes.RECT()
        _user32.GetClientRect(int(self.winId()), ctypes.byref(r))
        cw, ch = r.right - r.left, r.bottom - r.top
        self.client_px = (cw, ch)
        self.size_ok = (cw, ch) == self._phys
        if not self.size_ok:
            # Fractional DPR rounding: nudge the logical size until the physical client matches.
            dpr = self.devicePixelRatioF()
            lw =self.width() + round((self._phys[0] - cw) / dpr)
            lh = self.height() + round((self._phys[1] - ch) / dpr)
            if (lw, lh) != (self.width(), self.height()) and lw > 0 and lh > 0:
                self.setFixedSize(lw, lh)
                _user32.GetClientRect(int(self.winId()), ctypes.byref(r))
                cw, ch = r.right - r.left, r.bottom - r.top
                self.client_px = (cw, ch)
                self.size_ok = (cw, ch) == self._phys
        self.sizeVerified.emit(cw, ch, self.size_ok)
        return cw, ch, self.size_ok

    # --------------------------------------------------------------- running
    def start(self, fps: int) -> None:
        self._timer.start(max(2, int(1000 / (fps * 2))))  # poll at 2x rate, paint only new frames
        self.paused = False

    def stop(self) -> None:
        self._timer.stop()
        self.paused = True

    def _poll(self) -> None:
        self.tick_rate.tick()
        self.minimized = bool(_user32.IsIconic(int(self.winId())))
        if self.test_pattern:
            if self._pattern_arr is None or self._pattern_arr.shape[1::-1] != self._phys:
                from ..core.testpattern import make
                self._pattern_arr = make(*self._phys)
                qimg, arr = bgr_to_qimage_view(self._pattern_arr)
                qimg.setDevicePixelRatio(self.devicePixelRatioF())
                self._img, self._arr = qimg, arr
                self._seen = -1
                self.repaint()
            return
        if self._pattern_arr is not None:
            self._pattern_arr = None
            self._seen = -1
        out, v = self.pipeline.out_slot.peek()
        if out is None or v == self._seen:
            self.repeats += 1
            return
        self._seen = v
        qimg, arr = bgr_to_qimage_view(out.image)
        qimg.setDevicePixelRatio(self.devicePixelRatioF())
        self._img, self._arr = qimg, arr
        self._pending_capture_ns = out.t_capture_ns
        self._pending_clock = out.clock
        self.uniques += 1
        self.unique_rate.tick()
        self.repaint()  # synchronous paint so latency is measured at paint completion

    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        if self._img is None:
            p.fillRect(self.rect(), QColor(16, 16, 16))
        else:
            # 1:1 device pixels: image DPR == window DPR, target = full logical rect
            p.drawImage(QRect(0, 0, self.width(), self.height()), self._img)
        p.end()
        if self._pending_capture_ns is not None:
            if self._pending_clock in (ClockDomain.HOST_PERF, ClockDomain.BROWSER_CALIBRATED):
                self.paint_latency.add((now_ns() - self._pending_capture_ns) / 1e6)
            self._pending_capture_ns = None

    # -------------------------------------------------------------- moving
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._drag = None

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(50, self.verify_size)
        if self.windowHandle() is not None:
            try:
                self.windowHandle().screenChanged.connect(lambda *_: self._apply_size(),
                                                          Qt.ConnectionType.UniqueConnection)
            except Exception:
                pass

    def closeEvent(self, e) -> None:  # noqa: N802
        # The control window owns the lifetime; closing the output just hides it.
        self.stop()
        super().closeEvent(e)

    def stats(self) -> dict:
        lat = self.paint_latency.percentiles()
        return {"output_unique_fps": self.unique_rate.rate(), "output_uniques": self.uniques,
                "output_poll_repeats": self.repeats, "client_px": self.client_px, "size_ok": self.size_ok,
                "minimized": self.minimized, "paint_latency_p50": lat[50], "paint_latency_p95": lat[95],
                "paint_latency_p99": lat[99]}
