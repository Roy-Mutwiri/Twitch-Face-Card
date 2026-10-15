"""Headless pipeline run against a real source (no GUI), for validation.

Example:
  python tools/headless_run.py --title summit1g --panel 245,451,262,149 --seconds 20 --mode fast
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faceframe.capture import windows as win  # noqa: E402
from faceframe.capture.wgc import WgcSource  # noqa: E402
from faceframe.core.enhance import QualityMode, QualitySettings  # noqa: E402
from faceframe.core.geometry import Rect  # noqa: E402
from faceframe.core.pipeline import Pipeline, PipelineConfig  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="substring of the Edge window title")
    ap.add_argument("--panel", required=True, help="x,y,w,h in window pixels")
    ap.add_argument("--seconds", type=float, default=15)
    ap.add_argument("--mode", default="fast", choices=["original", "fast", "enhanced"])
    ap.add_argument("--model", default="realesr-general-x4v3")
    ap.add_argument("--out", default="1280x720")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--save", default=str(ROOT / "evidence" / "headless"))
    ap.add_argument("--select", default="auto", help="auto | x,y (panel px)")
    args = ap.parse_args()

    win.ensure_dpi_awareness()
    cands = [w for w in win.list_edge_windows() if args.title in w.title]
    if len(cands) != 1:
        print("need exactly one matching window:", [w.title for w in cands])
        return 2
    ow, oh = (int(v) for v in args.out.split("x"))
    q = QualitySettings(mode=QualityMode(args.mode), sr_model=args.model)
    pipe = Pipeline(ROOT / "models", PipelineConfig(out_w=ow, out_h=oh, out_fps=args.fps, quality=q))
    if args.mode == "enhanced":
        from faceframe.core.sr_engine import SREngine
        eng = SREngine(args.model, ROOT / "models")
        print("SR:", eng.load())
        pipe.set_sr_engine(eng)
    src = WgcSource(cands[0].hwnd, 0)
    pipe.set_source(src)
    pipe.set_panel(Rect(*(float(v) for v in args.panel.split(","))))
    save = Path(args.save)
    save.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    selected = False
    crops = []
    out_seen = 0
    unique_out = 0
    while time.time() - t0 < args.seconds:
        time.sleep(0.05)
        if not selected:
            if args.select == "auto":
                c = pipe.tracker.single_clear_candidate()
                if c is not None:
                    pipe.select_detection(c.box)
                    selected = True
                    print(f"[{time.time() - t0:5.1f}s] auto-selected face {c.box}")
            else:
                x, y = (float(v) for v in args.select.split(","))
                if pipe.tracker.candidates and pipe.select_person_at(x, y):
                    selected = True
                    print(f"[{time.time() - t0:5.1f}s] selected at {x},{y}")
        o, v = pipe.out_slot.peek()
        if o is not None and v != out_seen:
            unique_out += 1
            out_seen = v
            if o.crop is not None:
                crops.append((o.t_done_ns, o.crop.as_tuple(), o.state))
    st = pipe.status()
    o, _ = pipe.out_slot.peek()
    if o is not None:
        cv2.imwrite(str(save / f"output_{args.mode}.png"), o.image)
    lf = pipe.latest_frame
    if lf is not None:
        cv2.imwrite(str(save / "panel_native_capture.png"), lf.pixels)
    pipe.shutdown()
    st["unique_outputs_observed_by_poller"] = unique_out
    st["crop_track_len"] = len(crops)
    # jitter: frame-to-frame crop centre movement (px, panel space)
    import numpy as np
    if len(crops) > 3:
        c = np.array([[r[0] + r[2] / 2, r[1] + r[3] / 2, r[3]] for _, r, _ in crops])
        d = np.abs(np.diff(c, axis=0))
        st["crop_center_step_px_p50"] = float(np.percentile(np.hypot(d[:, 0], d[:, 1]), 50))
        st["crop_center_step_px_p95"] = float(np.percentile(np.hypot(d[:, 0], d[:, 1]), 95))
        st["crop_h_step_px_p95"] = float(np.percentile(d[:, 2], 95))
    print(json.dumps(st, indent=1, default=str))
    (save / f"status_{args.mode}.json").write_text(json.dumps(st, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
