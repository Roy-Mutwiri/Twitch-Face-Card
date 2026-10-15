import cv2
import numpy as np

from faceframe.core.testpattern import analyse, make


def canvas_with(img, x, y, cw=1080, ch=1920):
    c = np.full((ch, cw, 3), 30, np.uint8)
    h, w = img.shape[:2]
    c[y:y + h, x:x + w] = img[: ch - y, : cw - x]
    return c


def test_pattern_exact_size():
    assert make(1080, 720).shape == (720, 1080, 3)
    assert make(1280, 720).shape == (720, 1280, 3)


def test_detects_1to1_even_after_lossy_encoding():
    c = canvas_with(make(1080, 720), 0, 100)
    _, enc = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, 70])
    r = analyse(cv2.imdecode(enc, 1), 1080, 720)
    assert r["verdict"].startswith("1:1")
    assert abs(r["placed_at"][0]) <= 1 and abs(r["placed_at"][1] - 100) <= 1


def test_detects_hidden_downscale():
    p = cv2.resize(make(1080, 720), (972, 648), interpolation=cv2.INTER_AREA)
    r = analyse(canvas_with(p, 50, 100), 1080, 720)
    assert r["verdict"].startswith("SCALED") and abs(r["scale_x"] - 0.9) < 0.005


def test_detects_clipping():
    p = make(1080, 720)[:, 80:]
    r = analyse(canvas_with(p, 0, 100), 1080, 720)
    assert "missing" in r["verdict"]
