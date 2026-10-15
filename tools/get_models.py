"""Download (if missing) and verify FaceFrame model files. Run once; afterwards
everything runs offline.

  YuNet face detector  - MIT licence, OpenCV Zoo
  realesr-general-x4v3 - BSD-3-Clause, Real-ESRGAN (general photographic, lightweight)
  RealESRGAN_x2plus    - BSD-3-Clause, Real-ESRGAN (general photographic)
Anime models are intentionally not offered (wrong prior for real faces).
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "realesr-general-x4v3.pth": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292"),
    "realesr-general-wdn-x4v3.pth": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth",
        "1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca"),
    "RealESRGAN_x2plus.pth": (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb"),
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    d = ROOT / "models"
    d.mkdir(exist_ok=True)
    bad = 0
    for name, (url, digest) in MODELS.items():
        p = d / name
        if not p.is_file():
            print(f"downloading {name} ...")
            urllib.request.urlretrieve(url, p)
        ok = sha256(p) == digest
        print(f"{name}: {'OK' if ok else 'CHECKSUM MISMATCH'}")
        bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
