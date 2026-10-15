# Scenario: controlled_native

- connected True; selected face Rect(x=92.90336608886719, y=9.894691467285156, w=50.45638656616211, h=65.7690200805664); meta path=canvas pathError=Error: unsupported VideoFrame format BGRA
- conversion: RGBA8 (browser-converted)
- panel after rendition switch: Rect(x=0, y=624, w=524, h=284)
- reconnected: True
- native stats: {"native_frames": 1232, "present_to_receive_p50": 3.595796, "present_to_receive_p95": 8.88694, "readback_p50": 1.899999976158142, "readback_p95": 2.799999952316284, "rx_fps": 0.0, "rx_MBps": null, "clock_calibrated": true, "clock_rtt_ms": 0.2255, "dropped_old_geometry": 0, "malformed": 0, "rejected_handshakes": 0, "rejected_tokens": 0}

| t | step | source | track | panel | score | neutral | panel rect | src size | cap fps | uniq fps | message / event |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1.9 | baseline | RUNNING | TRACKING | VALID | 0.98 | False | [0, 416, 350, 190] | video (1280, 720) | 28.3 | 28.3 | Extension connected / Webcam area confirmed (score 1.00) |
| 7.91 | rendition 1280x720 -> 1920x1080 | RUNNING | TRACKING | VALID | 0.99 | False | [0, 416, 350, 190] | video (1280, 720) | 18.1 | 17.6 | Extension connected / Webcam area confirmed (score 1.00) |
| 8.41 | rendition 1280x720 -> 1920x1080 | GEOMETRY_CHANGED | TRACKING | CHECKING | 0.99 | True | [0, 624, 524, 284] | video (1920, 1080) | 18.6 | 18.6 | Stream rendition changed 1280x720 -> 1920x1080 / Stream rendition changed 1280x720 -> 1920x1080 |
| 9.41 | rendition 1280x720 -> 1920x1080 | GEOMETRY_CHANGED | TRACKING | VALID | 1.0 | False | [0, 624, 524, 284] | video (1920, 1080) | 23.2 | 23.2 | Stream rendition changed 1280x720 -> 1920x1080 / Webcam area confirmed (score 1.00) |
| 13.92 | rendition back to 1280x720 | GEOMETRY_CHANGED | TRACKING | VALID | 1.0 | False | [0, 624, 524, 284] | video (1920, 1080) | 27.2 | 27.2 | Stream rendition changed 1280x720 -> 1920x1080 / Webcam area confirmed (score 1.00) |
| 14.42 | rendition back to 1280x720 | GEOMETRY_CHANGED | TRACKING | CHECKING | 1.0 | True | [0, 416, 348, 188] | video (1280, 720) | 26.7 | 26.7 | Stream rendition changed 1920x1080 -> 1280x720 / Stream rendition changed 1920x1080 -> 1280x720 |
| 15.42 | rendition back to 1280x720 | GEOMETRY_CHANGED | TRACKING | VALID | 1.0 | False | [0, 416, 348, 188] | video (1280, 720) | 25.9 | 25.9 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area confirmed (score 1.00) |
| 19.93 | ad break | GEOMETRY_CHANGED | TRACKING | VALID | 1.0 | False | [0, 416, 348, 188] | video (1280, 720) | 25.5 | 25.5 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area confirmed (score 1.00) |
| 20.43 | ad break | GEOMETRY_CHANGED | LOST | VALID | 0.14 | False | [0, 416, 348, 188] | video (1280, 720) | 23.5 | 19.5 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area confirmed (score 1.00) |
| 21.43 | ad break | GEOMETRY_CHANGED | LOST | INVALID | 0.14 | True | [0, 416, 348, 188] | video (1280, 720) | 19.7 | 7.1 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 25.94 | ad over | GEOMETRY_CHANGED | LOST | INVALID | 0.14 | True | [0, 416, 348, 188] | video (1280, 720) | 27.0 | 0.0 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 26.44 | ad over | GEOMETRY_CHANGED | TRACKING | INVALID | 0.99 | True | [0, 416, 348, 188] | video (1280, 720) | 25.8 | 20.7 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area no longer matches (layout/scene changed): showing neutral output — reselect th |
| 27.44 | ad over | GEOMETRY_CHANGED | TRACKING | VALID | 0.99 | False | [0, 416, 348, 188] | video (1280, 720) | 26.3 | 25.6 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area confirmed (score 0.99) |
| 31.95 | video element replaced | GEOMETRY_CHANGED | TRACKING | VALID | 0.99 | False | [0, 416, 348, 188] | video (1280, 720) | 25.1 | 25.1 | Stream rendition changed 1920x1080 -> 1280x720 / Webcam area confirmed (score 0.99) |
| 32.45 | video element replaced | GEOMETRY_CHANGED | TRACKING | CHECKING | 0.99 | True | [0, 416, 348, 188] | video (1280, 720) | 26.4 | 25.9 | Video element replaced by the page / Video element replaced by the page |
| 33.45 | video element replaced | GEOMETRY_CHANGED | TRACKING | VALID | 1.0 | False | [0, 416, 348, 188] | video (1280, 720) | 26.2 | 25.7 | Video element replaced by the page / Webcam area confirmed (score 1.00) |
| 37.98 | another tab selected (Twitch tab hidden) | GEOMETRY_CHANGED | TRACKING | VALID | 0.99 | False | [0, 416, 348, 188] | video (1280, 720) | 27.5 | 27.0 | Video element replaced by the page / Webcam area confirmed (score 1.00) |
| 38.48 | another tab selected (Twitch tab hidden) | STALLED | LOST | VALID | 0.99 | False | [0, 416, 348, 188] | video (1280, 720) | 20.3 | 19.8 | Tab hidden: Edge throttles video frames in background tabs / Webcam area confirmed (score 1.00) |
| 43.01 | tab selected again | RUNNING | TRACKING | VALID | 1.0 | False | [0, 416, 348, 188] | video (1280, 720) | 0.0 | 0.0 | Receiving native video frames / Webcam area confirmed (score 1.00) |
| 48.02 | FaceFrame bridge server stopped | CLOSED | SOURCE_UNAVAILABLE | VALID | 0.99 | False | [0, 416, 348, 188] | video (1280, 720) | 26.3 | 26.3 | Extension disconnected (tab closed, navigated, reloaded or stopped) / Webcam area confirmed (score 1.00) |
| 48.52 | FaceFrame bridge server stopped | CLOSED | SOURCE_UNAVAILABLE | VALID | 0.99 | True | [0, 416, 348, 188] | video (1280, 720) | 19.6 | 19.6 | Extension disconnected (tab closed, navigated, reloaded or stopped) / Webcam area confirmed (score 1.00) |
| 51.02 | bridge server restarted (auto-reconnect) | CLOSED | SOURCE_UNAVAILABLE | VALID | 0.99 | True | [0, 416, 348, 188] | video (1280, 720) | 0.0 | 0.0 | Extension disconnected (tab closed, navigated, reloaded or stopped) / Webcam area confirmed (score 1.00) |
| 52.53 | bridge server restarted (auto-reconnect) | RUNNING | TRACKING | VALID | 1.0 | False | [0, 416, 348, 188] | video (1280, 720) | 16.2 | 16.2 | Extension connected / Webcam area confirmed (score 1.00) |
| 59.03 | video element removed | RUNNING | TRACKING | VALID | 0.99 | False | [0, 416, 348, 188] | video (1280, 720) | 27.0 | 27.0 | Extension connected / Webcam area confirmed (score 1.00) |
| 59.53 | video element removed | CLOSED | SOURCE_UNAVAILABLE | VALID | 0.99 | True | [0, 416, 348, 188] | video (1280, 720) | 20.3 | 20.3 | Extension: element-lost / Webcam area confirmed (score 1.00) |