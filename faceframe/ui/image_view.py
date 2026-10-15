"""Aspect-preserving image view with overlays, rectangle selection and click picking.
All public coordinates are IMAGE pixel coordinates."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..core.geometry import Rect
from .qimage import bgr_to_qimage


@dataclass
class Overlay:
    rect: Rect
    color: str
    label: str = ""
    width: int = 2
    dashed: bool = False


class ImageView(QWidget):
    rectSelected = Signal(object)   # Rect (image px)
    clicked = Signal(float, float)  # image px

    def __init__(self, parent=None, placeholder: str = "") -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(200, 140)
        self.setMouseTracking(True)
        self._img = None
        self._size = (0, 0)
        self.overlays: list[Overlay] = []
        self.mode = "none"          # none | select | click
        self.placeholder = placeholder
        self.caption = ""
        self._drag_a: QPointF | None = None
        self._drag_b: QPointF | None = None
        self.smooth = False         # nearest-neighbour by default to show real pixels

    def set_image(self, bgr: np.ndarray | None) -> None:
        if bgr is not None and bgr is getattr(self, "_last_arr", None):
            self.update()          # same frame: only overlays may have changed
            return
        self._last_arr = bgr
        if bgr is None:
            self._img = None
            self._size = (0, 0)
        else:
            self._img = bgr_to_qimage(bgr)
            self._size = (bgr.shape[1], bgr.shape[0])
        self.update()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.setCursor(Qt.CursorShape.CrossCursor if mode in ("select", "click") else Qt.CursorShape.ArrowCursor)
        self._drag_a = self._drag_b = None
        self.update()

    # ----------------------------------------------------------- mapping
    def _target(self) -> QRectF:
        iw, ih = self._size
        if iw == 0:
            return QRectF()
        W, H = self.width(), self.height()
        s = min(W / iw, H / ih)
        w, h = iw * s, ih * s
        return QRectF((W - w) / 2, (H - h) / 2, w, h)

    def to_image(self, p: QPointF) -> tuple[float, float]:
        t = self._target()
        s = t.width() / self._size[0]
        x = (p.x() - t.x()) / s
        y = (p.y() - t.y()) / s
        return (min(max(x, 0.0), self._size[0]), min(max(y, 0.0), self._size[1]))

    def to_widget(self, r: Rect) -> QRectF:
        t = self._target()
        s = t.width() / self._size[0] if self._size[0] else 1.0
        return QRectF(t.x() + r.x * s, t.y() + r.y * s, r.w * s, r.h * s)

    # ------------------------------------------------------------- paint
    def paintEvent(self, ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0B0D11"))
        if self._img is None:
            p.setPen(QColor(150, 150, 160))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.placeholder)
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth)
        p.drawImage(self._target(), self._img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        for o in self.overlays:
            pen = QPen(QColor(o.color), o.width)
            if o.dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            r = self.to_widget(o.rect)
            p.drawRect(r)
            if o.label:
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(o.label) + 14
                pill = QRectF(r.x(), r.y() - 22, tw, 19)
                col = QColor(o.color)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(pill, 9, 9)
                p.setPen(QColor("#0F1115") if col.lightness() > 140 else QColor("white"))
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter, o.label)
                p.setBrush(Qt.BrushStyle.NoBrush)
        if self._drag_a is not None and self._drag_b is not None:
            p.setPen(QPen(QColor("#ffd400"), 2, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(self._drag_a, self._drag_b).normalized())
        if self.caption:
            fm = p.fontMetrics()
            cap = QRectF(10, self.height() - 34, fm.horizontalAdvance(self.caption) + 20, 24)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(15, 17, 21, 210))
            p.drawRoundedRect(cap, 12, 12)
            p.setPen(QColor("#C9CFDB"))
            p.drawText(cap, Qt.AlignmentFlag.AlignCenter, self.caption)
        p.end()

    # ------------------------------------------------------------- mouse
    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self._img is None or e.button() != Qt.MouseButton.LeftButton:
            return
        if self.mode == "select":
            self._drag_a = self._drag_b = e.position()
        elif self.mode == "click":
            x, y = self.to_image(e.position())
            self.clicked.emit(x, y)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self.mode == "select" and self._drag_a is not None:
            self._drag_b = e.position()
            self.update()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self.mode == "select" and self._drag_a is not None:
            a = self.to_image(self._drag_a)
            b = self.to_image(e.position())
            self._drag_a = self._drag_b = None
            r = Rect.from_points(a[0], a[1], b[0], b[1])
            self.update()
            if r.w >= 8 and r.h >= 8:
                self.rectSelected.emit(r)
