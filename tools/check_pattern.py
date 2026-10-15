"""Check a LIVE Studio local recording (or screenshot) for hidden scaling/clipping
of the FaceFrame test pattern.

  python tools/check_pattern.py <recording.mp4|frame.png> --size 1080x720 [--at-second 5]

Note: the LIVE Studio canvas is itself re-encoded (HEVC/H.264), so a perfect
255 line contrast is not expected even at 1:1; the scale estimate is the
primary signal, contrast is secondary.
"""

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from faceframe.core.testpattern import analyse  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--at-second", type=float, default=3.0)
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.split("x"))
    if a.path.lower().endswith((".png", ".jpg", ".bmp")):
        frame = cv2.imread(a.path)
    else:
        cap = cv2.VideoCapture(a.path)
        cap.set(cv2.CAP_PROP_POS_MSEC, a.at_second * 1000)
        ok, frame = cap.read()
        if not ok:
            print("could not read a frame")
            return 2
    print(json.dumps({"frame": frame.shape[1::-1], **analyse(frame, w, h)}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
