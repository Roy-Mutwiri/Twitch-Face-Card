"""Bridge server security/protocol tests over a REAL loopback socket.
(The browser side is validated separately in the isolated Edge instance.)"""

from __future__ import annotations

import json
import struct
import time

import numpy as np
import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from faceframe.capture.bridge import (DEFAULT_EXTENSION_ID, HDR, HEADER_SIZE, MAX_MESSAGE, BridgeServer, BridgeSource,
                                      HeaderError, parse_header)
from faceframe.capture.yuv import bgr_to_i420, i420_to_bgr
from faceframe.core.geometry import Rect

TOKEN = "t" * 43
ORIGIN = f"chrome-extension://{DEFAULT_EXTENSION_ID}"


def frame_bytes(w=64, h=32, fmt=2, panel=(10, 20), video=(1280, 720), seq=1, payload=None, flags=(1 << 1)):
    if payload is None:
        payload = bytes(w * h * 3 // 2) if fmt != 1 else bytes(w * h * 4)
    hdr = HDR.pack(b"FFv1", 1, fmt, flags, seq, w, h, 1, video[0], video[1], panel[0], panel[1], w, h,
                   time.time() * 1000, 1.0, 1.5, len(payload))
    return hdr + bytes(HEADER_SIZE - len(hdr)) + payload


@pytest.fixture
def server():
    s = BridgeServer(47898, TOKEN)
    s.start()
    assert not s.error
    yield s
    s.stop()


class Sink:
    def __init__(self):
        self.frames, self.ovs, self.status = [], [], []

    def on_frame(self, f):
        self.frames.append(f)

    def on_overview(self, o):
        self.ovs.append(o)

    def on_status(self, s):
        self.status.append(s)


def test_rejects_page_origin(server):
    with pytest.raises(InvalidStatus) as e:
        connect("ws://127.0.0.1:47898/ff", origin="https://www.twitch.tv")
    assert e.value.response.status_code == 403
    with pytest.raises(InvalidStatus):
        connect("ws://127.0.0.1:47898/ff", origin="chrome-extension://someotherextensionidxxxxxxxxxxxxx")
    with pytest.raises(InvalidStatus):
        connect("ws://127.0.0.1:47898/other", origin=ORIGIN)
    assert server.rejected_handshakes == 3


def test_verified_extension_is_paired_automatically(server):
    ws = connect("ws://127.0.0.1:47898/ff", origin=ORIGIN)
    ws.send(json.dumps({"type": "hello", "token": ""}))       # never paired
    m = json.loads(ws.recv(timeout=3))
    assert m == {"type": "paired", "token": TOKEN}
    assert json.loads(ws.recv(timeout=3))["type"] == "welcome"
    assert server.auto_paired == 1 and server.connected
    ws.close()


def test_binary_before_auth_rejected(server):
    ws = connect("ws://127.0.0.1:47898/ff", origin=ORIGIN)
    ws.send(frame_bytes())
    with pytest.raises(ConnectionClosed):
        ws.recv(timeout=3)


def test_valid_i420_frame_decoded_with_correct_colours(server):
    src = BridgeSource(server)
    sink = Sink()
    src.start(sink)
    src._panel = Rect(10, 20, 64, 32)
    ws = connect("ws://127.0.0.1:47898/ff", origin=ORIGIN)
    ws.send(json.dumps({"type": "hello", "token": TOKEN}))
    assert json.loads(ws.recv(timeout=3))["type"] == "welcome"
    # colour bars: B, G, R, white, gray, black ...
    bars = np.zeros((32, 64, 3), np.uint8)
    cols = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (128, 128, 128), (0, 0, 0), (40, 160, 220),
            (200, 100, 30)]
    for i, c in enumerate(cols):
        bars[:, i * 8:(i + 1) * 8] = c
    payload = bgr_to_i420(bars, "bt709", False)
    ws.send(frame_bytes(payload=payload))
    acked = False
    t0 = time.time()
    while time.time() - t0 < 3 and not acked:
        m = json.loads(ws.recv(timeout=3))
        acked = m.get("type") == "ack" and m.get("seq") == 1
    assert acked
    assert len(sink.frames) == 1
    f = sink.frames[0]
    assert f.pixels.shape == (32, 64, 3) and f.video_size == (1280, 720)
    # compare centres of bars (edges blur from 4:2:0 chroma) -> channel order and matrix correct
    for i, c in enumerate(cols):
        got = f.pixels[16, i * 8 + 4].astype(int)
        assert np.abs(got - np.array(c)).max() <= 6, (i, got, c)
    ws.close()
    src.stop()


def test_malformed_and_oversize(server):
    src = BridgeSource(server)
    src.start(Sink())
    ws = connect("ws://127.0.0.1:47898/ff", origin=ORIGIN, max_size=None)
    ws.send(json.dumps({"type": "hello", "token": TOKEN}))
    ws.recv(timeout=3)
    bad = bytearray(frame_bytes())
    bad[0:4] = b"XXXX"
    ws.send(bytes(bad))
    m = None
    t0 = time.time()
    while time.time() - t0 < 3:
        m = json.loads(ws.recv(timeout=3))
        if m.get("type") == "drop":
            break
    assert m["type"] == "drop" and server.malformed == 1
    # payload length lie
    ws.send(frame_bytes()[:-10])
    time.sleep(0.2)
    assert server.malformed == 2
    # oversize message -> connection closed by the server (1009)
    with pytest.raises(ConnectionClosed):
        ws.send(b"\0" * (MAX_MESSAGE + 1024))
        for _ in range(10):
            ws.recv(timeout=3)
    src.stop()


def test_parse_header_rejects_bad_geometry():
    with pytest.raises(HeaderError):
        parse_header(frame_bytes(panel=(1270, 700)))      # rect outside video
    with pytest.raises(HeaderError):
        parse_header(frame_bytes(w=63, h=32, fmt=2, payload=bytes(63 * 32 * 3 // 2)))  # odd 4:2:0
    h = parse_header(frame_bytes())
    assert h.width == 64 and h.rect == (10, 20, 64, 32)


def test_i420_roundtrip_accuracy():
    # natural-image-like content (smooth colour fields): 4:2:0 + matrix round trip is near-lossless.
    # (Hard chroma edges lose detail in ANY 4:2:0 pipeline; the colour-bar test covers matrix/order.)
    import cv2
    rng = np.random.default_rng(0)
    img = cv2.resize(rng.integers(16, 235, (8, 12, 3), dtype=np.uint8), (96, 64), interpolation=cv2.INTER_CUBIC)
    for matrix in ("bt709", "bt601"):
        out = i420_to_bgr(bgr_to_i420(img, matrix), 96, 64, matrix, False)
        assert np.abs(out.astype(int) - img.astype(int)).mean() < 2.0
    # using the wrong matrix is measurably worse (proves the matrix matters)
    wrong = i420_to_bgr(bgr_to_i420(img, "bt709"), 96, 64, "bt601", False)
    assert np.abs(wrong.astype(int) - img.astype(int)).mean() > 2.0


def test_clock_sync_offset():
    from faceframe.capture.bridge import ClockSync
    c = ClockSync()
    # host perf ns = browser_ms*1e6 + 5e9 ; rtt 0.4 ms
    for i in range(5):
        hs = 10_000_000_000 + i * 1_000_000
        b = (hs + 200_000 - 5_000_000_000) / 1e6
        c.add(hs, b, hs + 400_000)
    assert c.calibrated
    assert c.to_host_ns(1000.0) == pytest.approx(1000.0 * 1e6 + 5e9, abs=1000)
