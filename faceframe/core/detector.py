"""Face detection on a working copy of the panel.

Detector: OpenCV YuNet (face_detection_yunet_2023mar.onnx, MIT licence,
~230 KB, 5-point landmarks). Chosen over MediaPipe because it ships inside the
already-required OpenCV wheel (no extra runtime), handles small/medium faces,
and runs in a few milliseconds on CPU, leaving the GPU for enhancement.

Detection runs on a resized working copy; boxes are mapped back to full panel
coordinates. The working copy is never used for output.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from .geometry import Rect
from .tracking import Detection

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


class YuNetDetector:
    def __init__(self, model_path: str | Path, score_threshold: float = 0.6,
                 work_long_side: int = 480, min_long_side: int = 320) -> None:
        model_path = Path(model_path)
        if not model_path.is_file():
            raise FileNotFoundError(f"face model missing: {model_path}")
        self.model_path = str(model_path)
        self.score_threshold = score_threshold
        self.work_long_side = work_long_side
        self.min_long_side = min_long_side
        self._net = cv2.FaceDetectorYN.create(self.model_path, "", (320, 320),
                                              score_threshold, 0.3, 50)
        self._size = (320, 320)
        self.last_ms = 0.0

    def working_scale(self, w: int, h: int) -> float:
        long_side = max(w, h)
        if long_side > self.work_long_side:
            return self.work_long_side / long_side
        if long_side < self.min_long_side:
            # Upscaling the *detector input* helps tiny faces; output never uses it.
            return self.min_long_side / long_side
        return 1.0

    def detect(self, panel_bgr: np.ndarray) -> list[Detection]:
        t0 = time.perf_counter()
        h, w = panel_bgr.shape[:2]
        s = self.working_scale(w, h)
        if s != 1.0:
            ww, wh = max(1, int(round(w * s))), max(1, int(round(h * s)))
            work = cv2.resize(panel_bgr, (ww, wh),
                              interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
        else:
            work, ww, wh = panel_bgr, w, h
        if (ww, wh) != self._size:
            self._net.setInputSize((ww, wh))
            self._size = (ww, wh)
        _, faces = self._net.detect(np.ascontiguousarray(work))
        out: list[Detection] = []
        if faces is not None:
            inv = 1.0 / s
            for f in faces:
                x, y, fw, fh = (float(v) * inv for v in f[:4])
                lm = tuple((float(f[4 + 2 * i]) * inv, float(f[5 + 2 * i]) * inv) for i in range(5))
                box = Rect(x, y, fw, fh).intersect(Rect(0, 0, w, h))
                if box.w < 4 or box.h < 4:
                    continue
                out.append(Detection(box, float(f[14]), lm))
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        return out
