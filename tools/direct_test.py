"""Headless test of the direct Twitch stream source.
  python tools/direct_test.py --channel summit1g --seconds 25 [--panel x,y,w,h]   (panel in video px)"""
import argparse, json, sys, time
from pathlib import Path
import cv2
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from faceframe.capture.direct import DirectStreamSource
from faceframe.core.geometry import Rect
from faceframe.core.pipeline import Pipeline, PipelineConfig

ap = argparse.ArgumentParser()
ap.add_argument("--channel", default="summit1g"); ap.add_argument("--seconds", type=float, default=25)
ap.add_argument("--panel", default=""); ap.add_argument("--quality", default="best"); ap.add_argument("--delay", type=float, default=0)
a = ap.parse_args()
out = ROOT / "evidence" / "direct"; out.mkdir(parents=True, exist_ok=True)
pipe = Pipeline(ROOT / "models", PipelineConfig())
src = DirectStreamSource(a.channel, a.quality, a.delay)
t0 = time.time(); pipe.set_source(src)
while pipe.latest_overview is None and time.time() - t0 < 40: time.sleep(0.2)
print(f"first overview after {time.time()-t0:.1f}s; rendition {src.rendition} {src.video_size}")
cv2.imwrite(str(out / "overview.png"), pipe.latest_overview.pixels)
if a.panel:
    pipe.set_panel(Rect(*(float(v) for v in a.panel.split(","))))
    t1 = time.time(); sel = False
    while time.time() - t1 < a.seconds:
        time.sleep(0.1)
        if not sel and pipe.tracker.candidates:
            pipe.select_detection(max(pipe.tracker.candidates, key=lambda d: d.box.area).box); sel = True
    st = pipe.status(); st.update(decoded=src.decoded, dropped_late=src.dropped_late)
    cv2.imwrite(str(out / "panel.png"), pipe.latest_frame.pixels)
    o, _ = pipe.out_slot.peek(); cv2.imwrite(str(out / "output.png"), o.image)
    print(json.dumps({k: st[k] for k in ("source_state", "track_state", "panel_validity", "capture_fps", "unique_source_fps",
          "process_fps", "latency_p50", "latency_p95", "panel_px", "video_size", "crop_px", "scale", "decoded", "dropped_late")}, default=str, indent=1))
pipe.shutdown()
