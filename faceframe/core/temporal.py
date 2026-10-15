"""Motion-compensated temporal fusion of the webcam panel.

Recursive filter in panel coordinates: the running fused image is warped onto
the current frame with dense optical flow (OpenCV DIS) and blended per pixel.
The blend weight falls off with the local warp error, so wherever alignment is
poor (fast motion, occlusion, mouth/eye changes) the CURRENT frame wins and
nothing is smeared; static or well-aligned areas accumulate several frames,
which averages out compression noise/blocking and adds sub-pixel information.

This uses only transmitted frames (no generative model): it cannot invent
detail, it can only combine what several frames really contain.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np


class TemporalFuser:
    def __init__(self, strength: float = 0.75, sigma: float = 5.0, flow_scale: float = 1.0) -> None:
        self.strength = strength      # max history weight (0.75 ~ effective 4 frames)
        self.sigma = sigma            # error (8-bit gray levels) at which history weight drops to e^-1
        self.flow_scale = flow_scale
        self._dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)
        # reset() comes from the UI thread (new panel/source) while process() runs on the
        # pipeline thread: one lock keeps the history consistent (process takes a few ms)
        self._lock = threading.Lock()
        self._clear()

    def reset(self) -> None:
        with self._lock:
            self._clear()

    def _clear(self) -> None:
        self._acc: np.ndarray | None = None      # float32 BGR, fused panel
        self._prev_gray: np.ndarray | None = None
        self._grid: np.ndarray | None = None
        self.last_history_weight = 0.0

    def process(self, frame_bgr: np.ndarray) -> np.ndarray:
        with self._lock:
            return self._process(frame_bgr)

    def _process(self, frame_bgr: np.ndarray) -> np.ndarray:
        f = frame_bgr.astype(np.float32)
        g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if self._acc is None or self._acc.shape != f.shape or self.strength <= 0:
            self._acc, self._prev_gray = f, g
            h, w = g.shape
            gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
            self._grid = np.dstack([gx, gy])
            return frame_bgr
        # flow from the current frame to the previous one: for each current pixel,
        # where it was in the previous (fused) image
        # flow at half resolution (4x fewer pixels), upsampled: plenty for webcam motion
        gs = cv2.resize(g, (g.shape[1] // 2, g.shape[0] // 2), interpolation=cv2.INTER_AREA)
        ps = cv2.resize(self._prev_gray, (gs.shape[1], gs.shape[0]), interpolation=cv2.INTER_AREA)
        flow = cv2.resize(self._dis.calc(gs, ps, None), (g.shape[1], g.shape[0]), interpolation=cv2.INTER_LINEAR) * 2.0
        maps = self._grid + flow
        warped = cv2.remap(self._acc, maps[..., 0], maps[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        err = cv2.cvtColor(cv2.absdiff(warped, f), cv2.COLOR_BGR2GRAY)
        err = cv2.GaussianBlur(err, (0, 0), 1.2)
        # wgt = strength * exp(-(err/sigma)^2), all in OpenCV (SIMD, releases the GIL)
        err = cv2.multiply(err, err, scale=-1.0 / (self.sigma * self.sigma))
        wgt = cv2.exp(err) * self.strength
        wgt3 = cv2.merge([wgt, wgt, wgt])
        # acc = f + wgt * (warped - f)
        self._acc = cv2.add(f, cv2.multiply(cv2.subtract(warped, f), wgt3))
        self._prev_gray = g
        self.last_history_weight = float(cv2.mean(wgt)[0])
        return cv2.convertScaleAbs(self._acc)
