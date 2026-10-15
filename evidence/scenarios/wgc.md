# Scenario: wgc

- window hwnd 0x67e1024; initial panel (window px) [245, 454, 272, 148]
- person selected: True
- after stop: source=None, frames still arriving? capture fps 0.0

| t | step | source | track | panel | score | neutral | panel rect | src size | cap fps | uniq fps | message / event |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.54 | baseline | RUNNING | TRACKING | VALID | 1.0 | False | [245, 454, 272, 148] | (1586, 993) | 51.8 | 51.8 | Capturing / Webcam area confirmed (score 1.00) |
| 3.54 | baseline | RUNNING | LOST | VALID | 0.97 | False | [245, 454, 272, 148] | (1586, 993) | 52.6 | 52.6 | Capturing / Webcam area confirmed (score 1.00) |
| 4.04 | baseline | RUNNING | TRACKING | VALID | 0.74 | False | [245, 454, 272, 148] | (1586, 993) | 53.1 | 53.1 | Capturing / Webcam area confirmed (score 1.00) |
| 4.54 | moved window | RUNNING | LOST | VALID | 0.54 | False | [245, 454, 272, 148] | (1586, 993) | 53.1 | 53.1 | Capturing / Webcam area confirmed (score 1.00) |
| 8.55 | resized larger | RUNNING | LOST | VALID | 0.57 | False | [245, 454, 272, 148] | (1586, 993) | 53.8 | 53.8 | Capturing / Webcam area confirmed (score 1.00) |
| 9.05 | resized larger | GEOMETRY_CHANGED | LOST | CHECKING | 0.57 | True | [245, 454, 272, 148] | (1886, 1143) | 53.8 | 51.3 | Window size changed 1586x993 -> 1886x1143 / Window size changed 1586x993 -> 1886x1143 |
| 14.06 | resized larger | GEOMETRY_CHANGED | LOST | INVALID | 0.49 | True | [245, 454, 272, 148] | (1886, 1143) | 55.7 | 55.2 | Window size changed 1586x993 -> 1886x1143 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 16.56 | resized back | GEOMETRY_CHANGED | LOST | INVALID | 0.49 | True | [245, 454, 272, 148] | (1886, 1143) | 54.4 | 53.4 | Window size changed 1586x993 -> 1886x1143 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 17.07 | resized back | GEOMETRY_CHANGED | LOST | CHECKING | 0.49 | True | [245, 454, 272, 148] | (1586, 993) | 52.2 | 51.7 | Window size changed 1886x1143 -> 1586x993 / Window size changed 1886x1143 -> 1586x993 |
| 18.57 | resized back | GEOMETRY_CHANGED | LOST | INVALID | 0.55 | True | [245, 454, 272, 148] | (1586, 993) | 52.7 | 52.1 | Window size changed 1886x1143 -> 1586x993 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 24.07 | resized back | GEOMETRY_CHANGED | TRACKING | INVALID | 0.35 | True | [245, 454, 272, 148] | (1586, 993) | 53.7 | 53.7 | Window size changed 1886x1143 -> 1586x993 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 24.59 | minimized | GEOMETRY_CHANGED | LOST | INVALID | 0.45 | True | [245, 454, 272, 148] | (1586, 993) | 54.6 | 54.6 | Window size changed 1886x1143 -> 1586x993 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 25.09 | minimized | MINIMIZED | SOURCE_UNAVAILABLE | INVALID | 0.45 | True | [245, 454, 272, 148] | (1586, 993) | 41.2 | 41.2 | Edge window is minimized: no frames are rendered / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 29.61 | restored | MINIMIZED | SOURCE_UNAVAILABLE | INVALID | 0.45 | True | [245, 454, 272, 148] | (1586, 993) | 0.0 | 0.0 | Edge window is minimized: no frames are rendered / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 30.11 | restored | RUNNING | LOST | INVALID | 0.0 | True | [245, 454, 272, 148] | (1586, 993) | 50.8 | 2.0 | Capturing / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 31.62 | restored | RUNNING | TRACKING | INVALID | 0.5 | True | [245, 454, 272, 148] | (1586, 993) | 55.2 | 1.0 | Capturing / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 36.64 | stopped source | STOPPED | SOURCE_UNAVAILABLE | INVALID | 0.55 | True | [245, 454, 272, 148] | None | 53.0 | 53.0 | Source stopped / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 39.61 | restarted same window (Edge untouched) | RUNNING | TRACKING | VALID | 1.0 | False | [253, 453, 272, 148] | (1586, 993) | 50.7 | 50.7 | Capturing / Webcam area confirmed (score 1.00) |
| 44.93 | navigated tab to another page | RUNNING | TRACKING | VALID | 0.99 | False | [253, 453, 272, 148] | (1586, 993) | 53.6 | 53.1 | Capturing / Webcam area confirmed (score 1.00) |
| 45.43 | navigated tab to another page | RUNNING | LOST | VALID | 0.0 | False | [253, 453, 272, 148] | (1586, 993) | 50.3 | 40.7 | Capturing / Webcam area confirmed (score 1.00) |
| 46.44 | navigated tab to another page | RUNNING | LOST | INVALID | 0.05 | True | [253, 453, 272, 148] | (1586, 993) | 51.0 | 24.0 | Capturing / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 50.94 | navigated tab to another page | STALLED | LOST | INVALID | 0.16 | True | [253, 453, 272, 148] | (1586, 993) | 0.0 | 0.0 | No new frames for 1.6s / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 53.26 | navigated back | RUNNING | LOST | INVALID | 0.16 | True | [253, 453, 272, 148] | (1586, 993) | 55.5 | 0.0 | Capturing / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 56.77 | navigated back | RUNNING | TRACKING | INVALID | 0.97 | True | [253, 453, 272, 148] | (1586, 993) | 45.6 | 25.0 | Capturing / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 57.27 | navigated back | RUNNING | TRACKING | VALID | 0.97 | False | [253, 453, 272, 148] | (1586, 993) | 49.9 | 27.9 | Capturing / Webcam area confirmed (score 0.97) |