"""Record native-resolution webcam panel frames from the direct stream (test data).
  python tools/record_direct.py --channel summit1g --panel 0,624,506,284 --seconds 15 --out evidence/clip_direct"""
import argparse, json, sys, time
from pathlib import Path
import cv2
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from faceframe.capture.direct import DirectStreamSource
from faceframe.core.geometry import Rect

class Sink:
    def __init__(s, out): s.out = out; s.n = 0; s.ov = None; s.meta = []
    def on_frame(s, f):
        cv2.imwrite(str(s.out / f"panel_{s.n:05d}.png"), f.pixels); s.meta.append(f.size); s.n += 1
    def on_overview(s, o): s.ov = o
    def on_status(s, st): print(st.state.value, st.message, flush=True)

ap = argparse.ArgumentParser(); ap.add_argument("--channel"); ap.add_argument("--panel"); ap.add_argument("--seconds", type=float, default=15)
ap.add_argument("--out", default="evidence/clip_direct"); a = ap.parse_args()
out = ROOT / a.out; out.mkdir(parents=True, exist_ok=True)
for f in out.glob("panel_*.png"): f.unlink()
sink = Sink(out); src = DirectStreamSource(a.channel); src.start(sink)
src.set_panel(Rect(*(float(v) for v in a.panel.split(","))))
t0 = time.time()
while sink.n == 0 and time.time() - t0 < 40: time.sleep(0.1)
time.sleep(a.seconds); src.stop()
cv2.imwrite(str(out / "overview.png"), sink.ov.pixels)
print("frames", sink.n, "size", sink.meta[0] if sink.meta else None)
