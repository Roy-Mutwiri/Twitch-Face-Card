"""Known test pattern for verifying the capture chain (FaceFrame Output ->
LIVE Studio -> local recording) for hidden scaling or clipping.

Features (all in output pixels):
* 1-px alternating border (clipping of even one pixel row/column is visible)
* corner squares in four distinct colours (orientation, channel order)
* 1-px and 2-px black/white line gratings (any resampling blurs 1-px lines)
* centre cross and the configured size as text
"""

from __future__ import annotations

import cv2
import numpy as np

CORNERS = {"tl": (0, 0, 255), "tr": (0, 255, 0), "bl": (255, 0, 0), "br": (0, 255, 255)}  # BGR
CORNER = 48


def make(w: int, h: int) -> np.ndarray:
    img = np.full((h, w, 3), 96, np.uint8)
    # 1-px alternating border
    for t in range(2):
        col = (255, 255, 255) if t == 0 else (0, 0, 0)
        img[t, :] = col
        img[h - 1 - t, :] = col
        img[:, t] = col
        img[:, w - 1 - t] = col
    c = CORNER
    img[2:2 + c, 2:2 + c] = CORNERS["tl"]
    img[2:2 + c, w - 2 - c:w - 2] = CORNERS["tr"]
    img[h - 2 - c:h - 2, 2:2 + c] = CORNERS["bl"]
    img[h - 2 - c:h - 2, w - 2 - c:w - 2] = CORNERS["br"]
    # gratings
    gw, gh = min(256, w // 3), min(96, h // 5)
    y0 = h // 2 - gh - 10
    x0 = w // 2 - gw // 2
    g1 = np.zeros((gh, gw, 3), np.uint8)
    g1[:, ::2] = 255                        # 1-px vertical lines
    g2 = np.zeros((gh, gw, 3), np.uint8)
    g2[::2, :] = 255                        # 1-px horizontal lines
    g3 = np.zeros((gh, gw, 3), np.uint8)
    g3[:, (np.arange(gw) // 2) % 2 == 0] = 255  # 2-px lines
    img[y0:y0 + gh, x0 - gw - 10:x0 - 10] = g1
    img[y0:y0 + gh, x0:x0 + gw] = g2
    img[y0:y0 + gh, x0 + gw + 10:x0 + 2 * gw + 10] = g3
    cv2.line(img, (w // 2, h // 2 + 20), (w // 2, h - 60), (255, 255, 255), 1)
    cv2.line(img, (w // 4, (h * 3) // 4), ((w * 3) // 4, (h * 3) // 4), (255, 255, 255), 1)
    cv2.putText(img, f"FaceFrame test pattern {w}x{h}", (60, h - 30), cv2.FONT_HERSHEY_SIMPLEX,
                max(0.5, w / 1600), (255, 255, 255), 1, cv2.LINE_AA)
    return img


def _corner_centre(img: np.ndarray, bgr: tuple[int, int, int]) -> tuple[float, float, int] | None:
    diff = np.abs(img.astype(np.int16) - np.array(bgr, np.int16)).max(axis=2)
    m = (diff < 60).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return float(cent[k][0]), float(cent[k][1]), int(stats[k, cv2.CC_STAT_AREA])


def analyse(frame_bgr: np.ndarray, w: int, h: int) -> dict:
    """Locate the pattern in a captured/recorded frame via its four coloured
    corner squares; report scale, placement, clipping and 1-px line contrast."""
    out: dict = {"expected": [w, h]}
    cs = {k: _corner_centre(frame_bgr, c) for k, c in CORNERS.items()}
    out["corners_found"] = [k for k, v in cs.items() if v]
    if not all(cs.values()):
        out["verdict"] = ("pattern corners missing: " + ", ".join(k for k, v in cs.items() if not v)
                          + " -> the output is clipped/cropped in this capture (or the pattern is off)")
        return out
    ref_dx = (w - 4 - CORNER)          # centre-to-centre distance in the pattern
    ref_dy = (h - 4 - CORNER)
    sx = ((cs["tr"][0] - cs["tl"][0]) + (cs["br"][0] - cs["bl"][0])) / 2 / ref_dx
    sy = ((cs["bl"][1] - cs["tl"][1]) + (cs["br"][1] - cs["tr"][1])) / 2 / ref_dy
    x0 = cs["tl"][0] - (2 + CORNER / 2) * sx
    y0 = cs["tl"][1] - (2 + CORNER / 2) * sy
    out.update(scale_x=round(sx, 4), scale_y=round(sy, 4), placed_at=[round(x0, 1), round(y0, 1)],
               placed_size=[round(w * sx, 1), round(h * sy, 1)])
    gw, gh = min(256, w // 3), min(96, h // 5)
    gx = int(round(x0 + (w // 2 - gw // 2 - gw - 10) * sx))
    gy = int(round(y0 + (h // 2 - gh - 10) * sy))
    grat = frame_bgr[gy + 4:gy + int(gh * sy) - 4, gx + 4:gx + int(gw * sx) - 4]
    g = cv2.cvtColor(grat, cv2.COLOR_BGR2GRAY).astype(float) if grat.size else np.zeros((1, 1))
    contrast = float(np.abs(np.diff(g, axis=1)).mean()) if g.shape[1] > 1 else 0.0
    out["one_px_line_contrast"] = round(contrast, 1)
    unscaled = abs(sx - 1) < 0.005 and abs(sy - 1) < 0.005
    if unscaled and contrast > 150:
        out["verdict"] = "1:1: no scaling detected; 1-px lines preserved"
    elif unscaled:
        out["verdict"] = (f"geometry 1:1 but 1-px line contrast only {contrast:.0f}/255: blurred by "
                          "encoding/filtering, not by resizing")
    else:
        out["verdict"] = (f"SCALED by {sx:.3f} x {sy:.3f}: the FaceFrame output is being resampled. "
                          "Resize the source in LIVE Studio to 100% or change FaceFrame's output size "
                          f"to {round(w * sx) // 2 * 2}x{round(h * sy) // 2 * 2}")
    return out
