"""Render installer artwork from the app's own logo code:
faceframe.ico (multi-size), wizard images (BMP) for Inno Setup."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])
from faceframe.ui.theme import app_icon_pixmap  # noqa: E402

out = ROOT / "installer" / "assets"
out.mkdir(parents=True, exist_ok=True)

# multi-size .ico (Pillow assembles the sizes)
from PIL import Image  # noqa: E402
tmp = out / "_icon256.png"
app_icon_pixmap(256).save(str(tmp))
Image.open(tmp).save(out / "faceframe.ico", sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
                                                   (64, 64), (128, 128), (256, 256)])
tmp.unlink()
app_icon_pixmap(512).save(str(out / "faceframe_512.png"))


def wizard_image(w: int, h: int, path: Path) -> None:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    g = QLinearGradient(0, 0, w * 0.6, h)
    g.setColorAt(0.0, QColor("#2a1d6e"))
    g.setColorAt(0.55, QColor("#14182a"))
    g.setColorAt(1.0, QColor("#0F1115"))
    p.fillRect(img.rect(), g)
    logo = app_icon_pixmap(int(w * 0.62))
    p.drawPixmap(int((w - logo.width()) / 2), int(h * 0.12), logo)
    p.setPen(QColor("white"))
    f = QFont("Segoe UI", int(w * 0.11))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, h * 0.55, w, h * 0.1), Qt.AlignmentFlag.AlignCenter, "FaceFrame")
    f2 = QFont("Segoe UI", int(w * 0.052))
    p.setFont(f2)
    p.setPen(QColor("#b9b2e6"))
    p.drawText(QRectF(w * 0.08, h * 0.66, w * 0.84, h * 0.2), Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
               "Automatic webcam framing\nfor TikTok LIVE Studio")
    p.end()
    img.save(str(path), "BMP")


# Inno Setup 6 modern wizard: 164x314 (100 %) and 410x797 (250 %); small image 55x55 / 138x140
wizard_image(164, 314, out / "wizard.bmp")
wizard_image(410, 797, out / "wizard_250.bmp")
for sz, name in ((55, "wizard_small.bmp"), (138, "wizard_small_250.bmp")):
    img = QImage(sz, sz, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.drawPixmap(0, 0, app_icon_pixmap(sz))
    p.end()
    img.save(str(out / name), "BMP")
print("assets written to", out)
