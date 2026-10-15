"""Pure geometry: rectangles, normalized mapping, object-fit/letterbox mapping,
crop construction and clamping. No I/O, no Qt, deterministic and unit tested."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h > 0 else 0.0

    def translated(self, dx: float, dy: float) -> "Rect":
        return Rect(self.x + dx, self.y + dy, self.w, self.h)

    def scaled(self, sx: float, sy: float | None = None) -> "Rect":
        sy = sx if sy is None else sy
        return Rect(self.x * sx, self.y * sy, self.w * sx, self.h * sy)

    def intersect(self, other: "Rect") -> "Rect":
        x1, y1 = max(self.x, other.x), max(self.y, other.y)
        x2, y2 = min(self.x2, other.x2), min(self.y2, other.y2)
        return Rect(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))

    def iou(self, other: "Rect") -> float:
        inter = self.intersect(other).area
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def contains_point(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x2 and self.y <= py <= self.y2

    def to_int(self) -> tuple[int, int, int, int]:
        """Integer pixel rect that covers the same area (rounded, w/h >= 1)."""
        x = int(round(self.x))
        y = int(round(self.y))
        w = max(1, int(round(self.w)))
        h = max(1, int(round(self.h)))
        return x, y, w, h

    @staticmethod
    def from_points(x1: float, y1: float, x2: float, y2: float) -> "Rect":
        return Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)


# --------------------------------------------------------------------------- #
# Normalized coordinates (fractions of a surface), used for persisted settings.
# --------------------------------------------------------------------------- #

def to_normalized(r: Rect, surface_w: float, surface_h: float) -> Rect:
    return Rect(r.x / surface_w, r.y / surface_h, r.w / surface_w, r.h / surface_h)


def from_normalized(n: Rect, surface_w: float, surface_h: float) -> Rect:
    return Rect(n.x * surface_w, n.y * surface_h, n.w * surface_w, n.h * surface_h)


def clamp_rect_inside(r: Rect, bw: float, bh: float) -> Rect:
    """Shift (then, if still too large, shrink) ``r`` so it lies inside [0,bw]x[0,bh].
    Shrinking keeps the aspect ratio and the centre as close as possible."""
    w, h = r.w, r.h
    if w > bw or h > bh:
        s = min(bw / w if w > 0 else 1.0, bh / h if h > 0 else 1.0)
        w, h = w * s, h * s
        cx, cy = r.cx, r.cy
        r = Rect(cx - w / 2, cy - h / 2, w, h)
    x = min(max(r.x, 0.0), bw - w)
    y = min(max(r.y, 0.0), bh - h)
    return Rect(x, y, w, h)


def snap_crop_to_pixels(r: Rect, bw: int, bh: int) -> tuple[int, int, int, int]:
    """Integer crop fully inside the surface."""
    x0 = int(math.floor(max(0.0, r.x)))
    y0 = int(math.floor(max(0.0, r.y)))
    x1 = int(math.ceil(min(float(bw), r.x2)))
    y1 = int(math.ceil(min(float(bh), r.y2)))
    x1 = max(x1, x0 + 1)
    y1 = max(y1, y0 + 1)
    x1 = min(x1, bw)
    y1 = min(y1, bh)
    x0 = min(x0, x1 - 1)
    y0 = min(y0, y1 - 1)
    return x0, y0, x1 - x0, y1 - y0


# --------------------------------------------------------------------------- #
# object-fit / letterbox mapping
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FitMapping:
    """Maps content pixels into a container: container = content * scale + offset."""

    scale_x: float
    scale_y: float
    offset_x: float
    offset_y: float

    def content_to_container(self, r: Rect) -> Rect:
        return Rect(r.x * self.scale_x + self.offset_x, r.y * self.scale_y + self.offset_y,
                    r.w * self.scale_x, r.h * self.scale_y)

    def container_to_content(self, r: Rect) -> Rect:
        return Rect((r.x - self.offset_x) / self.scale_x, (r.y - self.offset_y) / self.scale_y,
                    r.w / self.scale_x, r.h / self.scale_y)


def object_fit_mapping(content_w: float, content_h: float, box_w: float, box_h: float,
                       fit: str = "contain", pos_x: float = 0.5, pos_y: float = 0.5) -> FitMapping:
    """CSS object-fit mapping of intrinsic content into a box.

    ``pos_x/pos_y`` are the CSS object-position fractions (0.5 = centre).
    Supports contain, cover, fill, none, scale-down — the same semantics the
    extension implements in JavaScript (extension/geometry.js).
    """
    if content_w <= 0 or content_h <= 0 or box_w <= 0 or box_h <= 0:
        raise ValueError("object_fit_mapping: non-positive size")
    if fit == "fill":
        sx, sy = box_w / content_w, box_h / content_h
    else:
        if fit == "contain":
            s = min(box_w / content_w, box_h / content_h)
        elif fit == "cover":
            s = max(box_w / content_w, box_h / content_h)
        elif fit == "none":
            s = 1.0
        elif fit == "scale-down":
            s = min(1.0, min(box_w / content_w, box_h / content_h))
        else:
            raise ValueError(f"unknown object-fit {fit!r}")
        sx = sy = s
    dw, dh = content_w * sx, content_h * sy
    return FitMapping(sx, sy, (box_w - dw) * pos_x, (box_h - dh) * pos_y)


def letterbox(src_w: int, src_h: int, dst_w: int, dst_h: int, mode: str = "fit") -> tuple[float, int, int, int, int]:
    """Placement of a src image inside a dst canvas without distortion.

    mode="fit": whole image visible, padding added. Returns (scale, x, y, w, h)
    of the drawn image in dst pixels (w/h rounded, never exceeding dst).
    """
    if mode != "fit":
        raise ValueError("letterbox only supports fit; fill is done by cropping")
    s = min(dst_w / src_w, dst_h / src_h)
    w = min(dst_w, max(1, int(round(src_w * s))))
    h = min(dst_h, max(1, int(round(src_h * s))))
    return s, (dst_w - w) // 2, (dst_h - h) // 2, w, h


# --------------------------------------------------------------------------- #
# Framing crop construction
# --------------------------------------------------------------------------- #

FRAMING_PRESETS = {
    # crop height as a multiple of detected face-box height, and where the face
    # centre sits vertically (fraction from top). YuNet boxes span roughly
    # brow-to-chin, so 0.40 leaves headroom above the hair and shows shoulders.
    "head_shoulders": {"height_mult": 2.6, "face_y": 0.42},
    "wide": {"height_mult": 4.2, "face_y": 0.38},
    "tight": {"height_mult": 1.9, "face_y": 0.46},
}


def target_crop_for_face(face: Rect, aspect: float, framing: str,
                         panel_w: float, panel_h: float, fill: bool = True) -> Rect:
    """Ideal crop (panel pixels) for a face box, before smoothing.

    aspect = output width / height. If the panel cannot hold a crop of that
    size, the crop is shrunk keeping aspect (fill=True: deliberate fill crop) or
    allowed to take the panel's own aspect (fill=False: rendered with padding).
    """
    p = FRAMING_PRESETS.get(framing, FRAMING_PRESETS["head_shoulders"])
    h = face.h * p["height_mult"]
    w = h * aspect
    # Never narrower than ~1.9 face widths (keeps ears/shoulders for portrait outputs).
    min_w = face.w * 1.9
    if w < min_w:
        w = min_w
        h = w / aspect
    cx = face.cx
    cy = face.y + face.h / 2.0 + (0.5 - p["face_y"]) * h
    crop = Rect(cx - w / 2, cy - h / 2, w, h)
    return fit_crop_to_panel(crop, panel_w, panel_h, fill)


def fit_crop_to_panel(crop: Rect, panel_w: float, panel_h: float, fill: bool = True) -> Rect:
    if fill:
        return clamp_rect_inside(crop, panel_w, panel_h)
    # fit mode: keep requested size where possible, clip to panel on each axis.
    w = min(crop.w, panel_w)
    h = min(crop.h, panel_h)
    x = min(max(crop.cx - w / 2, 0.0), panel_w - w)
    y = min(max(crop.cy - h / 2, 0.0), panel_h - h)
    return Rect(x, y, w, h)


def snap_crop_to_aspect(r: Rect, aspect: float, bw: int, bh: int) -> tuple[int, int, int, int]:
    """Integer crop inside the surface whose w/h is as close to ``aspect`` as
    integer pixels allow (fill mode), so the output needs no side padding."""
    h = max(2, min(bh, int(round(r.h))))
    w = int(round(h * aspect))
    if w > bw:
        w = bw
        h = max(2, min(bh, int(round(w / aspect))))
    w = max(2, w)
    x = int(round(r.cx - w / 2))
    y = int(round(r.cy - h / 2))
    x = min(max(x, 0), bw - w)
    y = min(max(y, 0), bh - h)
    return x, y, w, h


def even(v: int) -> int:
    v = int(v)
    return v if v % 2 == 0 else v + 1


def parse_aspect(text: str) -> float:
    """'16:9' -> 1.777..., '3:2' -> 1.5, '1.5' -> 1.5"""
    text = text.strip()
    if ":" in text:
        a, b = text.split(":", 1)
        return float(a) / float(b)
    return float(text)


def panel_rect_from_normalized_checked(n: Rect, surface_w: int, surface_h: int,
                                       min_px: int = 16) -> Rect | None:
    """Pixel panel from a stored normalized rect, or None if it is degenerate."""
    if n.w <= 0 or n.h <= 0 or n.x < -1e-6 or n.y < -1e-6 or n.x2 > 1 + 1e-6 or n.y2 > 1 + 1e-6:
        return None
    r = from_normalized(n, surface_w, surface_h)
    if r.w < min_px or r.h < min_px:
        return None
    return r
