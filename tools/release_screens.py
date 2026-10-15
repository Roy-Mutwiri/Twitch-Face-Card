"""Release screenshots of the real app on a live Twitch channel, with the stream picture blurred.

  python tools/release_screens.py --channel stylishnoob4 --panel 19,859,465,221

Writes docs/screenshots/{main,quality,audio,output,output_window}.png. Uses its own settings
file (never the user's) and plays no sound. Stream pixels in the preview are blurred and the output window shows the test
pattern, so published images don't show the streamer.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QTimer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from faceframe import settings as settings_mod  # noqa: E402
from faceframe.core.geometry import Rect  # noqa: E402
from faceframe.ui import app as appmod  # noqa: E402

OUT = ROOT / "docs" / "screenshots"


def qimg_to_bgr(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    h, w, bpl = img.height(), img.width(), img.bytesPerLine()
    a = np.frombuffer(img.constBits(), np.uint8, bpl * h).reshape(h, bpl)[:, : w * 3].reshape(h, w, 3)
    return cv2.cvtColor(a, cv2.COLOR_RGB2BGR)


def blur(a: np.ndarray, rects) -> np.ndarray:
    a = a.copy()
    for x, y, w, h in rects:
        x, y = max(0, x), max(0, y)
        roi = a[y:y + h, x:x + w]
        if roi.size == 0:
            continue
        small = cv2.resize(roi, (max(1, roi.shape[1] // 64), max(1, roi.shape[0] // 64)), interpolation=cv2.INTER_AREA)
        a[y:y + h, x:x + w] = cv2.GaussianBlur(cv2.resize(small, (roi.shape[1], roi.shape[0]),
                                                          interpolation=cv2.INTER_LINEAR), (0, 0), 14)
    return a


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="stylishnoob4")
    ap.add_argument("--panel", default="19,859,465,221")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    sp = Path(tempfile.gettempdir()) / "faceframe_screens_settings.json"
    sp.unlink(missing_ok=True)
    s, problems = settings_mod.load(sp)
    s.output.width, s.output.height, s.output.x, s.output.y = 1080, 720, 60, 60
    settings_mod.save(s, sp)
    orig_save = settings_mod.save
    settings_mod.save = lambda st, path=None: orig_save(st, sp)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    appmod.apply_palette(app)
    w = appmod.ControlWindow(s, problems)
    w._ensure_audio = lambda: None          # silent: never open an audio device
    w.resize(1500, 900)
    w.move(80, 40)
    w.show()

    def shot(name: str, blur_widgets=()):
        a = qimg_to_bgr(w.grab().toImage())
        rects = []
        for wid in blur_widgets:
            p = wid.mapTo(w, QPoint(0, 0))
            rects.append((p.x(), p.y(), wid.width(), wid.height()))
        cv2.imwrite(str(OUT / f"{name}.png"), blur(a, rects))
        print("saved", name, flush=True)

    def step_source():
        w.use_direct(f"https://www.twitch.tv/{args.channel}", "best")

    def step_panel():
        ov = w.pipeline.latest_overview
        if ov is None:
            QTimer.singleShot(500, step_panel)
            return
        w._apply_panel(Rect(*(float(v) for v in args.panel.split(","))), ov)
        w._best_preset()
        w._set_output_size(1080, 720)
        w.btn_output.setChecked(True)
        QTimer.singleShot(15000, shots)

    def shots():
        w.tabs.setCurrentIndex(1)
        w.edit_direct.setText("https://www.twitch.tv/<channel>")   # don't publish the channel name
        seq = [("main", 0), ("quality", 3), ("audio", 1), ("output", 4)]

        def nxt(i=0):
            if i >= len(seq):
                # the output window with FaceFrame's own test pattern (no stream content)
                w.chk_pattern.setChecked(True)
                QTimer.singleShot(1500, lambda: (cv2.imwrite(str(OUT / "output_window.png"),
                                                             qimg_to_bgr(w.output.grab().toImage())), w.close()))
                return
            name, page = seq[i]
            w.nav.setCurrentRow(page)
            QTimer.singleShot(900, lambda: (shot(name, [w.tabs.currentWidget()]), nxt(i + 1)))

        nxt()

    QTimer.singleShot(500, step_source)
    QTimer.singleShot(3000, step_panel)
    QTimer.singleShot(120000, app.quit)  # hard stop
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
