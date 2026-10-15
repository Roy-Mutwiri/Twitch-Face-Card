"""Fast vs Enhanced on IDENTICAL frames from the recorded native clip (evidence/clip).

Measures (no single sharpness score is used as a quality claim):
  A. reconstruction test: take native panel frames, area-downscale x2 (simulating
     a smaller source), upscale back x2 with each method, compare to the real
     native frame (PSNR / SSIM on the face crop) -> how much real detail each
     method recovers on THIS content under THIS degradation model.
  B. production setting: head-and-shoulders crop tracked over the clip, rendered
     to 1280x720 by each method:
       - back-projection consistency: output area-downscaled to the crop size vs the
         crop (PSNR) -> how far the output departs from what was transmitted
       - temporal flicker: mean |frame(t)-frame(t-1)| relative to Fast (1.0 = same)
       - SR latency per frame
  C. images: same-frame side-by-side + 4x nearest zoom of eyes/mouth.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faceframe.core.detector import YuNetDetector  # noqa: E402
from faceframe.core.enhance import QualityMode, QualitySettings, Renderer, resize_once  # noqa: E402
from faceframe.core.geometry import Rect, snap_crop_to_aspect, target_crop_for_face  # noqa: E402
from faceframe.core.sr_engine import SREngine  # noqa: E402
from faceframe.core.tracking import CropSmoother, SmootherParams  # noqa: E402

CLIP = ROOT / "evidence" / "clip"
OUT = ROOT / "evidence" / "quality"
OUT.mkdir(parents=True, exist_ok=True)


def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)


def ssim(a, b):
    a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float64)
    b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = cv2.GaussianBlur(a, (11, 11), 1.5), cv2.GaussianBlur(b, (11, 11), 1.5)
    va = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a ** 2
    vb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b ** 2
    cov = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    m = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))
    return float(m.mean())


def load_frames(n=None):
    files = sorted(CLIP.glob("panel_*.png"))[:n]
    return [cv2.imread(str(f)) for f in files]


def main() -> None:
    frames = load_frames()
    print("frames", len(frames), frames[0].shape)
    det = YuNetDetector(ROOT / "models" / "face_detection_yunet_2023mar.onnx")
    engines = {}
    for name in ("realesr-general-x4v3", "RealESRGAN_x2plus"):
        e = SREngine(name, ROOT / "models")
        engines[name] = (e, e.load())
    results: dict = {"frames": len(frames), "panel": frames[0].shape[1::-1]}

    # ---------------- face track over the clip (detector every 3rd frame ~10 Hz)
    faces = []
    last = None
    for i, f in enumerate(frames):
        if i % 3 == 0:
            ds = det.detect(f)
            if ds:
                if last is None:
                    best = max(ds, key=lambda d: d.box.area)
                else:
                    best = min(ds, key=lambda d: abs(d.box.cx - last.cx) + abs(d.box.cy - last.cy))
                last = best.box
        faces.append(last)

    # ---------------- A: reconstruction test on the face region (x2)
    methods_a = {
        "bicubic": lambda s, w, h: cv2.resize(s, (w, h), interpolation=cv2.INTER_CUBIC),
        "lanczos": lambda s, w, h: cv2.resize(s, (w, h), interpolation=cv2.INTER_LANCZOS4),
        "fast(cubic+sharpen0.25)": None,
        "x4v3": lambda s, w, h: resize_once(engines["realesr-general-x4v3"][0].run(s), w, h),
        "x2plus": lambda s, w, h: resize_once(engines["RealESRGAN_x2plus"][0].run(s), w, h),
    }
    from faceframe.core.enhance import unsharp
    methods_a["fast(cubic+sharpen0.25)"] = lambda s, w, h: unsharp(
        cv2.resize(s, (w, h), interpolation=cv2.INTER_CUBIC), 0.9 * 0.25, max(0.6, 0.5 * 2))
    rec = {k: {"psnr": [], "ssim": []} for k in methods_a}
    for i in range(0, len(frames), 22):
        f = frames[i]
        fc = faces[i]
        if fc is None:
            continue
        # face-centred 96x96 region (even), clipped to panel
        cx, cy = int(fc.cx), int(fc.cy)
        x0 = max(0, min(f.shape[1] - 96, cx - 48)) & ~1
        y0 = max(0, min(f.shape[0] - 96, cy - 48)) & ~1
        gt = f[y0:y0 + 96, x0:x0 + 96]
        small = cv2.resize(gt, (48, 48), interpolation=cv2.INTER_AREA)
        for k, fn in methods_a.items():
            up = fn(small, 96, 96)
            rec[k]["psnr"].append(psnr(up, gt))
            rec[k]["ssim"].append(ssim(up, gt))
    results["A_reconstruction_x2_face96"] = {k: {"psnr_mean": float(np.mean(v["psnr"])),
                                                 "ssim_mean": float(np.mean(v["ssim"])),
                                                 "n": len(v["psnr"])} for k, v in rec.items()}

    # ---------------- B: production crops at 1280x720
    sm = CropSmoother(SmootherParams(smoothness=0.55))
    crops = []
    for i, f in enumerate(frames):
        fc = faces[i]
        tgt = target_crop_for_face(fc, 16 / 9, "head_shoulders", f.shape[1], f.shape[0], True) if fc else \
            Rect(0, 0, f.shape[1], f.shape[0])
        r = sm.update(tgt, 1 / 30) if i else sm.snap(tgt)
        crops.append(snap_crop_to_aspect(r, 16 / 9, f.shape[1], f.shape[0]))
    variants = {
        "fast_sharpen0": QualitySettings(mode=QualityMode.FAST, sharpen=0.0),
        "fast_sharpen0.25": QualitySettings(mode=QualityMode.FAST, sharpen=0.25),
        "enh_x4v3_s1.0": QualitySettings(mode=QualityMode.ENHANCED, enhance_strength=1.0, auto_fallback=False),
        "enh_x4v3_s0.6": QualitySettings(mode=QualityMode.ENHANCED, enhance_strength=0.6, auto_fallback=False),
        "enh_x2plus_s1.0": QualitySettings(mode=QualityMode.ENHANCED, enhance_strength=1.0, auto_fallback=False,
                                           sr_model="RealESRGAN_x2plus"),
    }
    seg = range(300, 420)  # 4 s of motion used for temporal statistics
    stats = {}
    keep = {}
    for name, q in variants.items():
        eng = engines["RealESRGAN_x2plus" if "x2plus" in name else "realesr-general-x4v3"][0]
        r = Renderer(eng)
        prev = None
        diffs, bp, srms = [], [], []
        for i in seg:
            x, y, w, h = crops[i]
            crop = frames[i][y:y + h, x:x + w]
            out, info = r.render(crop, 1280, 720, q, fill=True)
            srms.append(info.sr_ms)
            g = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                diffs.append(float(np.mean(np.abs(g - prev))))
            prev = g
            back = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
            bp.append(psnr(back, crop))
            if i in (330, 360, 400):
                keep[(name, i)] = (out, crop, (x, y, w, h))
        stats[name] = {"temporal_mean_abs_diff": float(np.mean(diffs)), "backprojection_psnr": float(np.mean(bp)),
                       "sr_ms_p50": float(np.percentile(srms, 50)), "sr_ms_p95": float(np.percentile(srms, 95)),
                       "crop_size_example": crops[360][2:]}
    base = stats["fast_sharpen0"]["temporal_mean_abs_diff"]
    for k in stats:
        stats[k]["flicker_vs_fast"] = stats[k]["temporal_mean_abs_diff"] / base
    results["B_production_1280x720"] = stats

    # ---------------- C: images
    for i in (330, 360, 400):
        row = [keep[(n, i)][0] for n in ("fast_sharpen0.25", "enh_x4v3_s0.6", "enh_x4v3_s1.0", "enh_x2plus_s1.0")]
        crop = keep[("fast_sharpen0", i)][1]
        orig = np.full((720, 1280, 3), 16, np.uint8)
        ch, cw = crop.shape[:2]
        orig[(720 - ch) // 2:(720 - ch) // 2 + ch, (1280 - cw) // 2:(1280 - cw) // 2 + cw] = crop
        labels = ["original 1:1", "fast (sharpen .25)", "enhanced x4v3 s0.6", "enhanced x4v3 s1.0", "x2plus s1.0"]
        tiles = [orig] + row
        for t, l in zip(tiles, labels):
            cv2.putText(t, l, (12, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 2)
        grid = np.vstack([np.hstack(tiles[0:2]), np.hstack(tiles[2:4]),
                          np.hstack([tiles[4], np.full_like(tiles[4], 16)])])
        cv2.imwrite(str(OUT / f"same_frame_{i}.png"), grid)
        # zoom on the face (eyes/mouth) at 2x output pixels, nearest
        fc = faces[i]
        x, y, w, h = keep[("fast_sharpen0", i)][2]
        s = 1280 / w
        fx, fy = int((fc.x - x) * s), int((fc.y - y) * s)
        fw, fh = int(fc.w * s), int(fc.h * s)
        zooms = []
        for t, l in zip(row, labels[1:]):
            z = t[max(0, fy):fy + fh, max(0, fx):fx + fw]
            z = cv2.resize(z, (360, int(360 * z.shape[0] / max(1, z.shape[1]))), interpolation=cv2.INTER_NEAREST)
            cv2.putText(z, l, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
            zooms.append(z)
        hmin = min(z.shape[0] for z in zooms)
        cv2.imwrite(str(OUT / f"face_zoom_{i}.png"), np.hstack([z[:hmin] for z in zooms]))

    (OUT / "quality_metrics.json").write_text(json.dumps(results, indent=1, default=str))
    print(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
