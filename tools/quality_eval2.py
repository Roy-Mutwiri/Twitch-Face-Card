"""'Best of best' candidates on the direct-stream 1080p60 clip (evidence/clip_direct).

Methods (production: fixed head-and-shoulders crop -> 1280x720):
  F0  Fast: Lanczos + sharpen 0.25                         (current default)
  T1  temporal fusion (panel) + Lanczos + sharpen 0.35
  E1  Real-ESRGAN x4v3, strength 0.6                        (current Enhanced)
  E2  x4v3 with denoise 0.5 (DNI with wdn), strength 1.0
  TE  temporal fusion + x4v3 denoise 0.5, strength 0.7
Metrics:
  A  reconstruction: panel sequence area-downscaled x2, each method (incl. fusion
     over the small sequence) upscales x2, compared with the real 1080p frames on
     the face region -> PSNR / SSIM (how much REAL detail comes back)
  B  temporal noise: mean temporal std over 60 frames in a static background area
     of the 1280x720 output (lower = cleaner, compression shimmer removed)
  C  flicker vs F0 on the whole output, ms per frame
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
from faceframe.core.enhance import QualityMode, QualitySettings, Renderer, unsharp  # noqa: E402
from faceframe.core.geometry import snap_crop_to_aspect, target_crop_for_face  # noqa: E402
from faceframe.core.sr_engine import SREngine  # noqa: E402
from faceframe.core.temporal import TemporalFuser  # noqa: E402
from tools.quality_eval import psnr, ssim  # noqa: E402

CLIP = ROOT / "evidence" / "clip_direct"
OUT = ROOT / "evidence" / "quality_direct"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    frames = [cv2.imread(str(p)) for p in sorted(CLIP.glob("panel_*.png"))]
    ph, pw = frames[0].shape[:2]
    det = YuNetDetector(ROOT / "models" / "face_detection_yunet_2023mar.onnx")
    eng = {}
    for key, dn in (("dn1", 1.0), ("dn05", 0.5)):
        e = SREngine("realesr-general-x4v3", ROOT / "models", denoise=dn)
        e.load()
        eng[key] = e
    res: dict = {"frames": len(frames), "panel": [pw, ph]}

    seg = list(range(120, 300))
    # fixed crop from the face in the middle of the segment
    face = max(det.detect(frames[210]), key=lambda d: d.box.area).box
    x, y, w, h = snap_crop_to_aspect(target_crop_for_face(face, 16 / 9, "head_shoulders", pw, ph), 16 / 9, pw, ph)
    res["crop"] = [x, y, w, h]

    def run_method(name):
        fuse = name in ("T1", "TE")
        fz = TemporalFuser(0.75, 5.0)
        r = {"F0": Renderer(), "T1": Renderer(), "E1": Renderer(eng["dn1"]), "E2": Renderer(eng["dn05"]),
             "TE": Renderer(eng["dn05"])}[name]
        q = {"F0": QualitySettings(mode=QualityMode.FAST, sharpen=0.25, interpolation="lanczos"),
             "T1": QualitySettings(mode=QualityMode.FAST, sharpen=0.35, interpolation="lanczos"),
             "E1": QualitySettings(mode=QualityMode.ENHANCED, enhance_strength=0.6, interpolation="lanczos", auto_fallback=False),
             "E2": QualitySettings(mode=QualityMode.ENHANCED, enhance_strength=1.0, interpolation="lanczos", auto_fallback=False),
             "TE": QualitySettings(mode=QualityMode.ENHANCED, enhance_strength=0.7, interpolation="lanczos", auto_fallback=False)}[name]
        outs, ms = [], []
        for i in range(seg[0] - 20, seg[-1] + 1):   # 20 warm-up frames for the fuser
            t = time.perf_counter()
            p = fz.process(frames[i]) if fuse else frames[i]
            o, _ = r.render(p[y:y + h, x:x + w], 1280, 720, q, fill=True)
            ms.append((time.perf_counter() - t) * 1000)
            if i >= seg[0]:
                outs.append(o)
        return outs, float(np.percentile(ms[20:], 50))

    # ---------------- B/C production
    prod = {}
    for name in ("F0", "T1", "E1", "E2", "TE"):
        outs, ms = run_method(name)
        prod[name] = outs
        g = np.stack([cv2.cvtColor(o, cv2.COLOR_BGR2GRAY).astype(np.float32) for o in outs[:60]])
        # static background: right 20 % x bottom 35 % of the output (chair), person-free in this crop
        bg = g[:, int(720 * 0.65):, int(1280 * 0.80):]
        res.setdefault("B_temporal_noise_static_bg", {})[name] = round(float(bg.std(axis=0).mean()), 3)
        d = np.mean([np.abs(g[k] - g[k - 1]).mean() for k in range(1, len(g))])
        res.setdefault("C_mean_abs_frame_diff", {})[name] = round(float(d), 3)
        res.setdefault("C_ms_per_frame_p50", {})[name] = round(ms, 1)
    base = res["C_mean_abs_frame_diff"]["F0"]
    res["C_flicker_vs_F0"] = {k: round(v / base, 3) for k, v in res["C_mean_abs_frame_diff"].items()}

    # ---------------- A reconstruction x2 on the face region
    small = [cv2.resize(f, (pw // 2, ph // 2), interpolation=cv2.INTER_AREA) for f in frames]
    fx0, fy0 = int(max(0, face.x - face.w * 0.3)), int(max(0, face.y - face.h * 0.3))
    fx1, fy1 = int(min(pw, face.x2 + face.w * 0.3)), int(min(ph, face.y2 + face.h * 0.3))
    acc = {}
    for name in ("F0", "T1", "E1", "E2", "TE"):
        fz = TemporalFuser(0.75, 5.0)
        ps, ss = [], []
        for i in range(seg[0] - 20, seg[-1] + 1, 1):
            s_in = fz.process(small[i]) if name in ("T1", "TE") else small[i]
            if i < seg[0] or (i - seg[0]) % 6:
                continue
            if name in ("F0", "T1"):
                up = cv2.resize(s_in, (pw, ph), interpolation=cv2.INTER_LANCZOS4)
                up = unsharp(up, 0.9 * (0.25 if name == "F0" else 0.35), 1.0)
            else:
                e = eng["dn1"] if name == "E1" else eng["dn05"]
                sr = cv2.resize(e.run(s_in), (pw, ph), interpolation=cv2.INTER_AREA)
                lz = cv2.resize(s_in, (pw, ph), interpolation=cv2.INTER_LANCZOS4)
                a = {"E1": 0.6, "E2": 1.0, "TE": 0.7}[name]
                up = cv2.addWeighted(sr, a, lz, 1 - a, 0)
            gt = frames[i][fy0:fy1, fx0:fx1]
            ps.append(psnr(up[fy0:fy1, fx0:fx1], gt))
            ss.append(ssim(up[fy0:fy1, fx0:fx1], gt))
        acc[name] = {"psnr": round(float(np.mean(ps)), 2), "ssim": round(float(np.mean(ss)), 4), "n": len(ps)}
    res["A_reconstruction_x2_face"] = acc

    # ---------------- images
    k = 90
    labels = {"F0": "F0 fast (current)", "T1": "T1 fusion+lanczos", "E1": "E1 enhanced (current)",
              "E2": "E2 SR denoise .5", "TE": "TE fusion+SR"}
    tiles = []
    for name in ("F0", "T1", "E1", "TE"):
        t = prod[name][k].copy()
        cv2.putText(t, labels[name], (12, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 2)
        tiles.append(t)
    cv2.imwrite(str(OUT / "same_frame_grid.png"), np.vstack([np.hstack(tiles[:2]), np.hstack(tiles[2:])]))
    fxo = int((face.x - x) * 1280 / w)
    fyo = int((face.y - y) * 720 / h)
    fwo = int(face.w * 1280 / w)
    fho = int(face.h * 720 / h)
    zs = []
    for name in ("F0", "T1", "E1", "TE"):
        z = prod[name][k][max(0, fyo - 20):fyo + fho + 20, max(0, fxo - 20):fxo + fwo + 20]
        z = cv2.resize(z, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_NEAREST)
        cv2.putText(z, name, (6, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        zs.append(z)
    hmin = min(z.shape[0] for z in zs)
    cv2.imwrite(str(OUT / "face_zoom.png"), np.hstack([z[:hmin] for z in zs]))
    (OUT / "metrics.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
