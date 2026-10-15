"""YUV 4:2:0 -> BGR with an explicit colour matrix and range (no OpenCV BT.601 assumption)."""

from __future__ import annotations

import cv2
import numpy as np

KR_KB = {"bt709": (0.2126, 0.0722), "bt601": (0.299, 0.114), "bt2020": (0.2627, 0.0593)}
MATRIX_CODES = {1: "bt709", 2: "bt601", 3: "bt2020"}


def pick_matrix(code: int, height: int) -> str:
    """Unknown matrix: HD (>=720 lines) is BT.709, SD is BT.601 (common decoder default)."""
    return MATRIX_CODES.get(code) or ("bt709" if height >= 720 else "bt601")


def _yuv_matrix(matrix: str, full_range: bool) -> np.ndarray:
    """3x4 affine matrix mapping (Y, U, V, 1) in 0..255 to (B, G, R)."""
    kr, kb = KR_KB[matrix]
    kg = 1.0 - kr - kb
    ys, cs, yo = (1.0, 1.0, 0.0) if full_range else (255.0 / 219.0, 255.0 / 224.0, 16.0)
    # R = ys(Y-yo) + 2(1-kr) cs (V-128); B = ys(Y-yo) + 2(1-kb) cs (U-128); G from the luma equation
    rv = 2.0 * (1.0 - kr) * cs
    bu = 2.0 * (1.0 - kb) * cs
    gu = -kb * bu / kg
    gv = -kr * rv / kg
    m = np.array([[ys, bu, 0.0], [ys, gu, gv], [ys, 0.0, rv]], np.float32)
    off = -m @ np.array([yo, 128.0, 128.0], np.float32)
    return np.hstack([m, off[:, None]]).astype(np.float32)


_MATS: dict = {}


def yuv420_to_bgr(y: np.ndarray, u: np.ndarray, v: np.ndarray, matrix: str = "bt709",
                  full_range: bool = False) -> np.ndarray:
    """One SIMD affine transform (cv2.transform) over the merged full-res YUV image."""
    h, w = y.shape
    key = (matrix, full_range)
    if key not in _MATS:
        _MATS[key] = _yuv_matrix(matrix, full_range)
    # chroma 4:2:0 -> full resolution (bilinear; siting differences are sub-sample)
    uf = cv2.resize(u, (w, h), interpolation=cv2.INTER_LINEAR)
    vf = cv2.resize(v, (w, h), interpolation=cv2.INTER_LINEAR)
    yuv = cv2.merge([np.ascontiguousarray(y), uf, vf]).astype(np.float32)
    out = cv2.transform(yuv, _MATS[key])
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def i420_to_bgr(buf: memoryview | bytes, w: int, h: int, matrix: str, full_range: bool) -> np.ndarray:
    a = np.frombuffer(buf, np.uint8)
    ys = w * h
    y = a[:ys].reshape(h, w)
    u = a[ys:ys + ys // 4].reshape(h // 2, w // 2)
    v = a[ys + ys // 4:ys + ys // 2].reshape(h // 2, w // 2)
    return yuv420_to_bgr(y, u, v, matrix, full_range)


def nv12_to_bgr(buf: memoryview | bytes, w: int, h: int, matrix: str, full_range: bool) -> np.ndarray:
    a = np.frombuffer(buf, np.uint8)
    ys = w * h
    y = a[:ys].reshape(h, w)
    uv = a[ys:ys + ys // 2].reshape(h // 2, w // 2, 2)
    return yuv420_to_bgr(y, np.ascontiguousarray(uv[..., 0]), np.ascontiguousarray(uv[..., 1]), matrix, full_range)


def bgr_to_i420(bgr: np.ndarray, matrix: str = "bt709", full_range: bool = False) -> bytes:
    """Reference encoder used by tests (inverse of yuv420_to_bgr)."""
    kr, kb = KR_KB[matrix]
    kg = 1.0 - kr - kb
    f = bgr.astype(np.float32)
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    y = kr * r + kg * g + kb * b
    u = (b - y) / (2.0 * (1.0 - kb))
    v = (r - y) / (2.0 * (1.0 - kr))
    if not full_range:
        y = y * (219.0 / 255.0) + 16.0
        u *= 224.0 / 255.0
        v *= 224.0 / 255.0
    u += 128.0
    v += 128.0
    h, w = y.shape
    u2 = cv2.resize(u, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    v2 = cv2.resize(v, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    q = lambda a: np.clip(a + 0.5, 0, 255).astype(np.uint8).tobytes()  # noqa: E731
    return q(y) + q(u2) + q(v2)
