"""Original / Fast / Enhanced rendering of the selected crop into the output canvas.

Rules enforced here:
* exactly one resampling step from crop to output in Fast mode;
* in Enhanced mode: SR at the model's native factor, then ONE final size
  conversion (no chains), then optional blend with the Fast result;
* sharpening is conservative and thresholded to avoid halos/noise boost;
* the crop is never stretched: fill crops are already at output aspect, fit
  crops are letterboxed with neutral padding.
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from .geometry import letterbox
from .metrics import Ema

log = logging.getLogger("faceframe.enhance")

PAD_COLOR = (16, 16, 16)


class QualityMode(str, enum.Enum):
    ORIGINAL = "original"
    FAST = "fast"
    ENHANCED = "enhanced"


@dataclass
class QualitySettings:
    mode: QualityMode = QualityMode.FAST
    sharpen: float = 0.25             # 0..1, Fast unsharp amount (conservative)
    enhance_strength: float = 0.6     # 0..1 blend SR vs Fast at output size (0.6: see evidence/quality)
    sr_model: str = "realesr-general-x4v3"
    interpolation: str = "cubic"      # cubic | lanczos
    sr_budget_ms: float = 45.0        # sustained SR time above this -> fallback
    auto_fallback: bool = True
    max_flicker_ratio: float = 1.6    # Enhanced frame-to-frame change vs Fast; above -> Fast


@dataclass
class RenderInfo:
    mode_requested: QualityMode
    mode_used: QualityMode
    crop_size: tuple[int, int]
    out_size: tuple[int, int]
    drawn_size: tuple[int, int]      # image area inside the canvas (excl. padding)
    scale: float                     # drawn / crop (linear)
    sr_ms: float = 0.0
    sr_out_size: tuple[int, int] | None = None
    total_ms: float = 0.0
    reason: str = ""
    warning: str = ""


def _interp(scale: float, pref: str) -> int:
    if scale < 1.0:
        return cv2.INTER_AREA
    return cv2.INTER_LANCZOS4 if pref == "lanczos" else cv2.INTER_CUBIC


def unsharp(img: np.ndarray, amount: float, sigma: float, threshold: int = 3) -> np.ndarray:
    """Thresholded unsharp mask; amount<=0 returns the input."""
    if amount <= 0:
        return img
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    sharp = cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)  # saturating uint8, SIMD
    if threshold > 0:
        # keep the original where local contrast is tiny (avoid boosting noise/blocking)
        diff = cv2.absdiff(img, blur)
        mask = cv2.compare(cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY), threshold, cv2.CMP_GT)
        out = img.copy()
        cv2.copyTo(sharp, mask, out)
        return out
    return sharp


def resize_once(img: np.ndarray, w: int, h: int, pref: str = "cubic") -> np.ndarray:
    ih, iw = img.shape[:2]
    if (iw, ih) == (w, h):
        return img
    return cv2.resize(img, (w, h), interpolation=_interp(w / iw, pref))


def place_on_canvas(img: np.ndarray, out_w: int, out_h: int, x: int, y: int) -> np.ndarray:
    canvas = np.empty((out_h, out_w, 3), np.uint8)
    canvas[:] = PAD_COLOR
    h, w = img.shape[:2]
    canvas[y:y + h, x:x + w] = img
    return canvas


def neutral_frame(out_w: int, out_h: int) -> np.ndarray:
    c = np.empty((out_h, out_w, 3), np.uint8)
    c[:] = PAD_COLOR
    return c


class Renderer:
    """Turns a crop into the final output frame. Owns the (optional) SR engine."""

    def __init__(self, sr_engine=None) -> None:
        self.sr = sr_engine
        self.sr_ms = Ema(0.15)
        self.over_budget_since: float | None = None
        self.fallback_reason = ""
        self.forced_fast = False
        # temporal stability probe (Enhanced vs Fast on the same frames)
        self._prev_probe: tuple[np.ndarray, np.ndarray] | None = None
        self.flicker_ratio = Ema(0.05)
        self._probe_n = 0

    def set_engine(self, engine) -> None:
        self.sr = engine
        self.forced_fast = False
        self.fallback_reason = ""
        self.over_budget_since = None
        self.sr_ms = Ema(0.15)
        self._prev_probe = None
        self.flicker_ratio = Ema(0.05)
        self._probe_n = 0

    def render(self, crop: np.ndarray, out_w: int, out_h: int, q: QualitySettings,
               probe_stability: bool = False, fill: bool = False) -> tuple[np.ndarray, RenderInfo]:
        t0 = time.perf_counter()
        ch, cw = crop.shape[:2]
        s, x, y, dw, dh = letterbox(cw, ch, out_w, out_h)
        if fill and abs((cw / ch) / (out_w / out_h) - 1.0) < 0.01:
            # fill crop already at output aspect up to integer rounding (<1%):
            # use the whole canvas instead of leaving 1-3 px padding bars.
            x, y, dw, dh = 0, 0, out_w, out_h
        mode = q.mode
        reason = ""
        if mode == QualityMode.ENHANCED:
            if self.forced_fast:
                mode, reason = QualityMode.FAST, self.fallback_reason
            elif self.sr is None or not getattr(self.sr, "ready", False):
                mode, reason = QualityMode.FAST, "Enhanced unavailable (GPU model not loaded)"
            elif s <= 1.05:
                mode, reason = QualityMode.FAST, "Crop already at/above output size; SR not needed"

        info = RenderInfo(q.mode, mode, (cw, ch), (out_w, out_h), (dw, dh), dw / cw, reason=reason)
        if s >= 2.0:
            info.warning = (f"Small source crop ({cw}x{ch}) enlarged {dw / cw:.1f}x; "
                            "enlargement cannot recover detail that was never transmitted")

        if mode == QualityMode.ORIGINAL:
            # 1:1 inspection: never upscale; downscale only if it does not fit.
            if cw <= out_w and ch <= out_h:
                img, dw, dh = crop, cw, ch
                x, y = (out_w - cw) // 2, (out_h - ch) // 2
            else:
                img = resize_once(crop, dw, dh)
                info.warning = "Original view downscaled to fit the canvas"
            info.drawn_size = (dw, dh)
            info.scale = dw / cw
            out = place_on_canvas(img, out_w, out_h, x, y)
        elif mode == QualityMode.FAST:
            img = resize_once(crop, dw, dh, q.interpolation)
            if q.sharpen > 0 and dw > cw:
                img = unsharp(img, amount=0.9 * q.sharpen, sigma=max(0.6, 0.5 * dw / cw))
            out = place_on_canvas(img, out_w, out_h, x, y)
        else:
            out = self._render_enhanced(crop, dw, dh, x, y, out_w, out_h, q, info, probe_stability)
        info.total_ms = (time.perf_counter() - t0) * 1000.0
        return out, info

    def _render_enhanced(self, crop, dw, dh, x, y, out_w, out_h, q, info, probe):
        t = time.perf_counter()
        a = max(0.0, min(1.0, q.enhance_strength))
        try:
            if hasattr(self.sr, "run_resized"):
                # GPU path: SR, final resize and blend on the GPU; one small transfer back
                img = self.sr.run_resized(crop, dw, dh, a)
                sr = None
                residual = (dw / crop.shape[1]) / max(1, getattr(self.sr, "scale", 1))
                if residual > 1.15:
                    # the output is larger than the model's native factor: the remaining
                    # enlargement is plain interpolation, so restore edge contrast gently
                    # (thresholded, so flat skin/noise is not amplified)
                    img = unsharp(img, amount=min(0.6, 0.35 * residual), sigma=0.6 * residual, threshold=2)
            else:
                sr = self.sr.run(crop)
        except Exception as e:  # SRError, CUDA errors: never take down the pipeline
            self.forced_fast = True
            self.fallback_reason = f"Enhanced disabled: {e}"
            log.warning(self.fallback_reason)
            info.mode_used = QualityMode.FAST
            info.reason = self.fallback_reason
            img = resize_once(crop, dw, dh, q.interpolation)
            return place_on_canvas(img, out_w, out_h, x, y)
        info.sr_ms = (time.perf_counter() - t) * 1000.0
        info.sr_out_size = (crop.shape[1] * getattr(self.sr, "scale", 1), crop.shape[0] * getattr(self.sr, "scale", 1))
        ema = self.sr_ms.add(info.sr_ms)
        fast_img = None
        if sr is not None:
            sr_img = resize_once(sr, dw, dh, "cubic")  # single final size conversion
            if a < 0.999 or probe:
                fast_img = resize_once(crop, dw, dh, q.interpolation)
            img = cv2.addWeighted(sr_img, a, fast_img, 1.0 - a, 0.0) if a < 0.999 else sr_img
        elif probe and self._probe_tick():
            fast_img = resize_once(crop, dw, dh, q.interpolation)
        if probe and fast_img is not None:
            self._probe(img, fast_img)
            fr = self.flicker_ratio.value
            if q.auto_fallback and fr is not None and self._probe_n > 90 and fr > q.max_flicker_ratio:
                self.forced_fast = True
                self.fallback_reason = (f"Enhanced output shimmers {fr:.2f}x more than Fast (limit "
                                        f"{q.max_flicker_ratio:.1f}x); switched to Fast")
                log.warning(self.fallback_reason)
        # latency guard with hysteresis: sustained 2 s over budget -> Fast
        now = time.perf_counter()
        if q.auto_fallback and ema > q.sr_budget_ms:
            if self.over_budget_since is None:
                self.over_budget_since = now
            elif now - self.over_budget_since > 2.0:
                self.forced_fast = True
                self.fallback_reason = (f"Enhanced too slow ({ema:.0f} ms > {q.sr_budget_ms:.0f} ms budget); "
                                        "switched to Fast")
                log.warning(self.fallback_reason)
        elif ema < 0.8 * q.sr_budget_ms:
            self.over_budget_since = None
        return place_on_canvas(img, out_w, out_h, x, y)

    def _probe(self, sr_img: np.ndarray, fast_img: np.ndarray) -> None:
        a = cv2.resize(cv2.cvtColor(sr_img, cv2.COLOR_BGR2GRAY), (96, 96), interpolation=cv2.INTER_AREA)
        b = cv2.resize(cv2.cvtColor(fast_img, cv2.COLOR_BGR2GRAY), (96, 96), interpolation=cv2.INTER_AREA)
        if self._prev_probe is not None:
            pa, pb = self._prev_probe
            d_sr = float(np.mean(cv2.absdiff(a, pa)))
            d_fast = float(np.mean(cv2.absdiff(b, pb)))
            if d_fast > 0.05:
                self.flicker_ratio.add(d_sr / d_fast)
                self._probe_n += 1
        self._prev_probe = (a, b)

    def _probe_tick(self) -> bool:
        """Shimmer probe every 3rd frame (it needs an extra CPU resize)."""
        self._probe_count = getattr(self, "_probe_count", 0) + 1
        return self._probe_count % 3 == 0

    def reset_fallback(self) -> None:
        self.forced_fast = False
        self.fallback_reason = ""
        self.over_budget_since = None
