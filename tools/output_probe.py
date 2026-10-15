"""Capture the "FaceFrame Output" window from a SEPARATE process with WGC (the
same mechanism window-capture consumers use) and report what a capturer gets:
frame size vs configured size, unique-frame rate, and an image for inspection.

  python tools/output_probe.py --seconds 10 [--expect 1280x720] [--save name]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from faceframe.capture import windows as win  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--expect", default="")
    ap.add_argument("--save", default="output_probe")
    ap.add_argument("--wait", type=float, default=20, help="seconds to wait for the window to appear")
    args = ap.parse_args()
    win.ensure_dpi_awareness()
    hwnd = None
    t0 = time.time()
    while hwnd is None and time.time() - t0 < args.wait:
        hwnd = win.find_window_by_title("FaceFrame Output")
        if hwnd and not win.user32.IsWindowVisible(hwnd):
            hwnd = None
        time.sleep(0.25)
    if hwnd is None:
        print(json.dumps({"error": "FaceFrame Output window not found"}))
        return 2
    from windows_capture import WindowsCapture

    frames: list[tuple[float, tuple[int, int], int]] = []
    last = {"img": None, "hash": None}
    lock = threading.Lock()
    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=int(hwnd))

    @cap.event
    def on_frame_arrived(frame, control):
        buf = frame.frame_buffer
        h, w = buf.shape[:2]
        small = buf[::16, ::16, :3]
        hsh = hash(small.tobytes())
        with lock:
            frames.append((time.perf_counter(), (w, h), hsh))
            last["img"] = cv2.cvtColor(buf, cv2.COLOR_BGRA2BGR)

    @cap.event
    def on_closed():
        pass

    ctl = cap.start_free_threaded()
    time.sleep(args.seconds)
    ctl.stop()
    ctl.wait()
    out: dict = {"hwnd": hwnd, "client_px": win.client_size(hwnd), "window_rect": win.frame_bounds(hwnd),
                 "dpi": win.window_dpi(hwnd), "minimized": win.is_minimized(hwnd)}
    if frames:
        sizes = sorted({f[1] for f in frames})
        uniq = [frames[0]]
        for f in frames[1:]:
            if f[2] != uniq[-1][2]:
                uniq.append(f)
        span = frames[-1][0] - frames[0][0]
        out.update({"captured_sizes": sizes, "wgc_frames": len(frames), "unique_frames": len(uniq),
                    "unique_fps": (len(uniq) - 1) / span if span > 0 else 0.0})
        if args.expect:
            ew, eh = (int(v) for v in args.expect.split("x"))
            out["size_matches_expected"] = sizes == [(ew, eh)]
        img = last["img"]
        if img is not None:
            p = ROOT / "evidence" / f"{args.save}.png"
            cv2.imwrite(str(p), img)
            out["saved"] = str(p)
    else:
        out["captured_sizes"] = []
        out["wgc_frames"] = 0
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
