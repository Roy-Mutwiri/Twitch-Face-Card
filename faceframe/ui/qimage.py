from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage, QPixmap


def bgr_to_qimage(arr: np.ndarray) -> QImage:
    """Wrap a BGR uint8 array as a QImage (deep-copied so the array may be reused)."""
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, int(arr.strides[0]), QImage.Format.Format_BGR888)
    return img.copy()


def bgr_to_qimage_view(arr: np.ndarray) -> tuple[QImage, np.ndarray]:
    """Zero-copy wrap; caller must keep the returned array alive while the image is used."""
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, int(arr.strides[0]), QImage.Format.Format_BGR888), arr


def bgr_to_pixmap(arr: np.ndarray) -> QPixmap:
    return QPixmap.fromImage(bgr_to_qimage(arr))
