# Scenario: controlled_wgc

- controlled clip page, WGC hwnd 0x440fea, panel [245, 449, 268, 146]
- selected face Rect(x=72.71107382774353, y=12.825694584846495, w=35.454170036315915, h=44.4008309841156)
- tracked face centre x range over 20 s baseline: 86..98 (panel width 274.24583333333334; poster face is at x>220)
- stale detections rejected: 0; switch-guard rejections (other faces ignored): 0

| t | step | source | track | panel | score | neutral | panel rect | src size | cap fps | uniq fps | message / event |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.35 | baseline 20 s | RUNNING | TRACKING | VALID | 1.0 | False | [245, 449, 268, 146] | (1586, 993) | 31.8 | 31.8 | Capturing / Webcam area confirmed (score 1.00) |
| 20.39 | page scrolled 120 px | RUNNING | TRACKING | VALID | 0.98 | False | [245, 449, 268, 146] | (1586, 993) | 29.5 | 29.5 | Capturing / Webcam area confirmed (score 1.00) |
| 20.89 | page scrolled 120 px | RUNNING | LOST | CHECKING | 0.19 | False | [245, 449, 268, 146] | (1586, 993) | 29.5 | 29.5 | Capturing / Checking webcam area… (score 0.19) |
| 21.39 | page scrolled 120 px | RUNNING | TRACKING | VALID | 0.98 | False | [245, 329, 268, 146] | (1586, 993) | 30.0 | 30.0 | Capturing / Webcam area confirmed (score 0.98) |
| 25.4 | scrolled back | RUNNING | TRACKING | VALID | 1.0 | False | [245, 329, 268, 146] | (1586, 993) | 30.1 | 30.1 | Capturing / Webcam area confirmed (score 0.98) |
| 25.9 | scrolled back | RUNNING | LOST | CHECKING | 0.13 | False | [245, 329, 268, 146] | (1586, 993) | 30.7 | 30.7 | Capturing / Checking webcam area… (score 0.13) |
| 26.4 | scrolled back | RUNNING | TRACKING | VALID | 0.98 | False | [245, 449, 268, 146] | (1586, 993) | 30.5 | 30.5 | Capturing / Webcam area confirmed (score 0.98) |
| 30.41 | window resized larger | RUNNING | TRACKING | VALID | 1.0 | False | [245, 449, 268, 146] | (1586, 993) | 29.6 | 29.6 | Capturing / Webcam area confirmed (score 0.98) |
| 30.91 | window resized larger | RUNNING | LOST | CHECKING | 1.0 | True | [245, 449, 268, 146] | (1886, 1143) | 29.2 | 28.7 | Capturing (1886x1143 after resize) / Window size changed 1586x993 -> 1886x1143 |
| 33.92 | window resized larger | RUNNING | TRACKING | VALID | 1.0 | False | [242, 546, 354, 193] | (1886, 1143) | 29.8 | 29.8 | Capturing (1886x1143 after resize) / Webcam area confirmed (score 0.95) |
| 39.43 | window resized back | RUNNING | TRACKING | VALID | 0.99 | False | [242, 546, 354, 193] | (1886, 1143) | 30.3 | 29.8 | Capturing (1886x1143 after resize) / Webcam area confirmed (score 0.95) |
| 39.93 | window resized back | RUNNING | LOST | CHECKING | 0.99 | True | [242, 546, 354, 193] | (1586, 993) | 29.6 | 29.1 | Capturing (1586x993 after resize) / Window size changed 1886x1143 -> 1586x993 |
| 42.93 | window resized back | RUNNING | TRACKING | VALID | 1.0 | False | [240, 448, 274, 149] | (1586, 993) | 30.0 | 30.0 | Capturing (1586x993 after resize) / Webcam area confirmed (score 0.93) |
| 48.44 | ad break (inset replaced) | RUNNING | TRACKING | VALID | 1.0 | False | [240, 448, 274, 149] | (1586, 993) | 30.1 | 30.1 | Capturing (1586x993 after resize) / Webcam area confirmed (score 0.93) |
| 48.94 | ad break (inset replaced) | RUNNING | LOST | VALID | 0.18 | False | [240, 448, 274, 149] | (1586, 993) | 30.0 | 23.0 | Capturing (1586x993 after resize) / Webcam area confirmed (score 0.93) |
| 49.94 | ad break (inset replaced) | RUNNING | LOST | INVALID | 0.18 | True | [240, 448, 274, 149] | (1586, 993) | 29.9 | 7.6 | Capturing (1586x993 after resize) / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 54.45 | ad over | RUNNING | LOST | INVALID | 0.18 | True | [240, 448, 274, 149] | (1586, 993) | 29.2 | 0.0 | Capturing (1586x993 after resize) / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 54.95 | ad over | RUNNING | TRACKING | INVALID | 1.0 | True | [240, 448, 274, 149] | (1586, 993) | 29.4 | 29.1 | Capturing (1586x993 after resize) / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 55.45 | ad over | RUNNING | TRACKING | VALID | 0.99 | False | [240, 448, 274, 149] | (1586, 993) | 29.9 | 29.5 | Capturing (1586x993 after resize) / Webcam area confirmed (score 0.99) |
| 60.47 | minimized | RUNNING | LOST | VALID | 1.0 | False | [240, 448, 274, 149] | (1586, 993) | 29.2 | 28.7 | Capturing / Webcam area confirmed (score 0.99) |
| 61.47 | minimized | MINIMIZED | SOURCE_UNAVAILABLE | VALID | 1.0 | True | [240, 448, 274, 149] | (1586, 993) | 14.6 | 14.1 | Edge window is minimized: no frames are rendered / Webcam area confirmed (score 0.99) |
| 64.52 | restored | MINIMIZED | SOURCE_UNAVAILABLE | VALID | 1.0 | True | [240, 448, 274, 149] | (1586, 993) | 0.0 | 0.0 | Edge window is minimized: no frames are rendered / Webcam area confirmed (score 0.99) |
| 65.02 | restored | RUNNING | TRACKING | VALID | 0.0 | False | [240, 448, 274, 149] | (1586, 993) | 28.6 | 28.6 | Capturing / Webcam area confirmed (score 0.99) |
| 70.52 | source window closed | RUNNING | TRACKING | VALID | 1.0 | False | [240, 448, 274, 149] | (1586, 993) | 29.5 | 29.5 | Capturing / Webcam area confirmed (score 0.99) |
| 71.02 | source window closed | CLOSED | SOURCE_UNAVAILABLE | VALID | 1.0 | True | [240, 448, 274, 149] | (1586, 993) | 21.7 | 21.7 | Source window was closed / Webcam area confirmed (score 0.99) |