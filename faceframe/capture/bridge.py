"""Native-video bridge: loopback WebSocket server for the FaceFrame Edge extension.

Security
--------
* binds 127.0.0.1 only; path must be /ff
* WebSocket Origin must be chrome-extension://<allowed id> (fixed by the
  manifest "key"); page origins are rejected during the handshake
* first message must be {"type":"hello","token":...}; constant-time compare.
  Missing/stale token from the verified extension origin -> automatic pairing
  (the code is sent back and kept in extension-only storage). Never logged.
* max message size bounded; every binary frame header is validated; malformed
  frames are rejected and counted, repeated violations close the connection
* exactly one active client; a newer authenticated activation supersedes it

Flow control: each binary frame is ACKed after it has been handed to the
pipeline (latest-value slot, so this never blocks). The extension keeps at most
``max_in_flight`` unacknowledged frames and drops new ones instead of queueing.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from ..core.frame import ClockDomain, Frame, Overview, SourceKind, now_ns
from ..core.geometry import Rect
from ..core.metrics import LatencyStats, RateCounter
from ..core.source_base import SourceSink, SourceState, SourceStatus
from .yuv import i420_to_bgr, nv12_to_bgr, pick_matrix

log = logging.getLogger("faceframe.bridge")

HEADER_SIZE = 64
HDR = struct.Struct("<4sBBHIHHIHHHHHHddfI")
assert HDR.size == 56
MAX_DIM = 4096
MAX_MESSAGE = HEADER_SIZE + 2048 * 2048 * 4  # 16 MiB + header
FMT_RGBA, FMT_I420, FMT_NV12 = 1, 2, 3
MSG_PANEL, MSG_OVERVIEW = 1, 2
DEFAULT_EXTENSION_ID = "fgniljaehfphdhnpcknnipakaakgakha"


@dataclass
class FrameHeader:
    msg_type: int
    fmt: int
    flags: int
    seq: int
    width: int
    height: int
    gen: int
    video_w: int
    video_h: int
    rect: tuple[int, int, int, int]
    t_present_ms: float
    media_time: float
    readback_ms: float
    payload: int


class HeaderError(ValueError):
    pass


def parse_header(msg: bytes | memoryview) -> FrameHeader:
    if len(msg) < HEADER_SIZE:
        raise HeaderError("short message")
    magic, mt, fmt, flags, seq, w, h, gen, vw, vh, px, py, pw, ph, tp, mtime, rb, payload = HDR.unpack_from(msg, 0)
    if magic != b"FFv1":
        raise HeaderError("bad magic")
    if mt not in (MSG_PANEL, MSG_OVERVIEW):
        raise HeaderError(f"bad message type {mt}")
    if fmt not in (FMT_RGBA, FMT_I420, FMT_NV12):
        raise HeaderError(f"bad pixel format {fmt}")
    if not (2 <= w <= MAX_DIM and 2 <= h <= MAX_DIM and 2 <= vw <= MAX_DIM and 2 <= vh <= MAX_DIM):
        raise HeaderError("dimensions out of range")
    if payload != len(msg) - HEADER_SIZE:
        raise HeaderError("payload length mismatch")
    expected = w * h * 4 if fmt == FMT_RGBA else (w * h * 3) // 2
    if fmt != FMT_RGBA and (w % 2 or h % 2):
        raise HeaderError("odd dimensions for 4:2:0")
    if payload != expected:
        raise HeaderError("payload size does not match format/dimensions")
    if px + pw > vw or py + ph > vh or pw < 1 or ph < 1:
        raise HeaderError("rect outside video")
    if mt == MSG_PANEL and (pw, ph) != (w, h):
        raise HeaderError("panel rect does not match pixel size")
    if not (np.isfinite(tp) and np.isfinite(mtime)):
        raise HeaderError("non-finite timestamp")
    return FrameHeader(mt, fmt, flags, seq, w, h, gen, vw, vh, (px, py, pw, ph), tp, mtime, float(rb), payload)


def decode_pixels(hdr: FrameHeader, payload: memoryview) -> tuple[np.ndarray, str]:
    """Owned BGR array + description of the conversion actually applied."""
    if hdr.fmt == FMT_RGBA:
        a = np.frombuffer(payload, np.uint8).reshape(hdr.height, hdr.width, 4)
        return cv2.cvtColor(a, cv2.COLOR_RGBA2BGR), "RGBA8 (browser-converted)"
    full = bool(hdr.flags & 1)
    matrix = pick_matrix((hdr.flags >> 1) & 7, hdr.video_h)
    rng = "full" if full else "limited"
    if hdr.fmt == FMT_I420:
        return i420_to_bgr(payload, hdr.width, hdr.height, matrix, full), f"I420 {matrix} {rng} (decoder samples)"
    return nv12_to_bgr(payload, hdr.width, hdr.height, matrix, full), f"NV12 {matrix} {rng} (decoder samples)"


class ClockSync:
    """Browser epoch-ms -> host perf_counter_ns via ping/pong, min-RTT sample."""

    def __init__(self) -> None:
        self.samples: deque[tuple[int, float]] = deque(maxlen=30)  # (rtt_ns, offset_ns)

    def add(self, host_send_ns: int, browser_ms: float, host_recv_ns: int) -> None:
        rtt = host_recv_ns - host_send_ns
        if rtt <= 0:
            return
        offset = host_send_ns + rtt / 2.0 - browser_ms * 1e6
        self.samples.append((rtt, offset))

    @property
    def calibrated(self) -> bool:
        return bool(self.samples) and min(s[0] for s in self.samples) < 5_000_000

    def best(self) -> tuple[float, float] | None:
        if not self.samples:
            return None
        rtt, off = min(self.samples)
        return rtt / 1e6, off

    def to_host_ns(self, browser_ms: float) -> int | None:
        b = self.best()
        if b is None or not self.calibrated:
            return None
        return int(browser_ms * 1e6 + b[1])


class BridgeServer:
    def __init__(self, port: int, token: str, allowed_ids: list[str] | None = None) -> None:
        self.port = port
        self._token = token.encode()
        self.allowed_ids = set(allowed_ids or [DEFAULT_EXTENSION_ID])
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server = None
        self._client = None
        self._client_id = 0
        self.client_info: dict = {}
        self.listener = None          # BridgeSource (or None)
        self.clock = ClockSync()
        self.rejected_handshakes = 0
        self.rejected_tokens = 0
        self.auto_paired = 0
        self.malformed = 0
        self.error = ""
        self._started = threading.Event()
        self.rx_rate = RateCounter()
        self.rx_bytes = 0
        self.last_meta: dict = {}

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ff-bridge", daemon=True)
        self._thread.start()
        self._started.wait(5)

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._serve())
        except OSError as e:
            self.error = f"Cannot listen on 127.0.0.1:{self.port}: {e}"
            log.error(self.error)
            self._started.set()

    async def _serve(self) -> None:
        from websockets.asyncio.server import serve

        async with serve(self._handler, "127.0.0.1", self.port, process_request=self._check_request,
                         max_size=MAX_MESSAGE, max_queue=4, compression=None, ping_interval=20) as server:
            self._server = server
            self._stop_fut = self.loop.create_future()
            self._started.set()
            log.info("bridge listening on 127.0.0.1:%d", self.port)
            await self._stop_fut

    def stop(self) -> None:
        if self.loop and getattr(self, "_stop_fut", None) is not None:
            self.loop.call_soon_threadsafe(lambda: self._stop_fut.done() or self._stop_fut.set_result(None))
        if self._thread:
            self._thread.join(timeout=3)

    @property
    def connected(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------ handshake
    def _check_request(self, connection, request):
        origin = request.headers.get("Origin", "")
        ok_origin = origin.startswith("chrome-extension://") and origin[len("chrome-extension://"):] in self.allowed_ids
        if request.path != "/ff" or not ok_origin:
            self.rejected_handshakes += 1
            log.warning("rejected handshake path=%s origin=%s", request.path, origin or "<none>")
            return connection.respond(403, "Forbidden\n")
        return None

    async def _handler(self, ws) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            hello = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            await ws.close(4400, "hello expected")
            return
        if hello.get("type") != "hello":
            await ws.close(4400, "hello expected")
            return
        tok = str(hello.get("token", "")).encode()
        if not hmac.compare_digest(tok, self._token):
            # The handshake already proved Origin == chrome-extension://<FaceFrame id>
            # (browser-set; web pages cannot forge it). Pair automatically: hand the
            # extension the current code; it stores it in extension-only storage.
            self.auto_paired += 1
            log.info("extension paired automatically (%s)", "first pairing" if not tok else "code refreshed")
            await ws.send(json.dumps({"type": "paired", "token": self._token.decode()}))
        old = self._client
        if old is not None:
            await old.close(4409, "superseded by a newer activation")
        self._client_id += 1
        cid = self._client_id
        self._client = ws
        self.clock = ClockSync()
        self.client_info = {"id": cid, "ua": str(hello.get("ua", ""))[:200], "since": time.time()}
        log.info("extension connected (client %d)", cid)
        await ws.send(json.dumps({"type": "welcome", "proto": 1, "client": cid}))
        lst = self.listener
        if lst is not None:
            lst._on_connect(cid)
        pinger = asyncio.create_task(self._pinger(ws))
        violations = 0
        try:
            async for msg in ws:
                if isinstance(msg, (bytes, bytearray)):
                    t_recv = now_ns()
                    try:
                        hdr = parse_header(msg)
                    except HeaderError as e:
                        self.malformed += 1
                        violations += 1
                        log.warning("malformed frame rejected: %s", e)
                        await ws.send(json.dumps({"type": "drop", "reason": "malformed"}))
                        if violations > 20:
                            await ws.close(1003, "too many malformed frames")
                            break
                        continue
                    self.rx_rate.tick()
                    self.rx_bytes += len(msg)
                    lst = self.listener
                    if lst is not None:
                        try:
                            lst._on_binary(hdr, memoryview(msg)[HEADER_SIZE:], t_recv)
                        except Exception:
                            log.exception("bridge frame handling failed")
                    await ws.send(json.dumps({"type": "ack", "seq": hdr.seq}))
                else:
                    try:
                        m = json.loads(msg)
                    except Exception:
                        violations += 1
                        continue
                    if not isinstance(m, dict):
                        continue
                    if m.get("type") == "pong":
                        try:
                            self.clock.add(int(m["h"]), float(m["b"]), now_ns())
                        except Exception:
                            pass
                        continue
                    if m.get("type") == "meta":
                        self.last_meta = m
                    lst = self.listener
                    if lst is not None:
                        lst._on_event(m)
        except Exception as e:
            log.info("extension connection ended: %s", e)
        finally:
            pinger.cancel()
            if self._client is ws:
                self._client = None
                lst = self.listener
                if lst is not None:
                    lst._on_disconnect(cid)
            log.info("extension disconnected (client %d)", cid)

    async def _pinger(self, ws) -> None:
        try:
            for i in range(10_000_000):
                await ws.send(json.dumps({"type": "ping", "h": str(now_ns())}))
                await asyncio.sleep(0.25 if i < 12 else 2.0)
        except Exception:
            pass

    # -------------------------------------------------------------- sending
    def send(self, m: dict) -> bool:
        ws, loop = self._client, self.loop
        if ws is None or loop is None:
            return False
        asyncio.run_coroutine_threadsafe(self._send(ws, json.dumps(m)), loop)
        return True

    @staticmethod
    async def _send(ws, s: str) -> None:
        try:
            await ws.send(s)
        except Exception:
            pass


class BridgeSource:
    """Pipeline source backed by the extension (decoded <video> pixels)."""

    kind = SourceKind.NATIVE_VIDEO

    def __init__(self, server: BridgeServer, session_id: int = 0, fps: int = 30, path: str = "videoframe") -> None:
        self.server = server
        self.session_id = session_id
        self.fps = fps
        self.path = path
        self.source_id = "native:pending"
        self._sink: SourceSink | None = None
        self._panel: Rect | None = None
        self._lock = threading.Lock()
        self._seq = 0
        self.video_size: tuple[int, int] | None = None
        self.colour = ""
        self.gen = 0
        self.overview_hz = 0.0
        self._last_pseudo_ov = 0.0
        self._last_frame_t = 0.0
        self._last_frame_bgr: np.ndarray | None = None
        self.present_to_receive = LatencyStats()
        self.readback_ms = LatencyStats()
        self.frames = 0
        self.dropped_gen = 0
        self.state = SourceState.IDLE
        self._stop = threading.Event()
        self._watch: threading.Thread | None = None

    # -------------------------------------------------------------- control
    def describe(self) -> dict:
        return {"kind": self.kind.value, "video_size": self.video_size, "colour": self.colour,
                "client": self.server.client_info, "clock_calibrated": self.server.clock.calibrated,
                "clock_best_rtt_ms": (self.server.clock.best() or (None,))[0],
                "meta": {k: self.server.last_meta.get(k) for k in ("href", "title", "path", "pathError", "visibility",
                                                                   "protectedMedia", "rvfc", "videoFrame")},
                "note": "Decoded <video> pixels read by the extension (not window capture)."}

    def start(self, sink: SourceSink) -> None:
        self._sink = sink
        self._stop.clear()
        self.server.listener = self
        if self.server.connected:
            self._on_connect(self.server.client_info.get("id", 0))
        else:
            self._status(SourceState.WAITING, "Waiting for the Edge extension",
                         "In the Twitch tab click the FaceFrame Bridge toolbar button > Activate, then confirm the player")
        self._watch = threading.Thread(target=self._watchdog, name="bridge-watch", daemon=True)
        self._watch.start()

    def stop(self) -> None:
        self._stop.set()
        self.server.send({"type": "set_panel", "rect": None})
        if self.server.listener is self:
            self.server.listener = None
        if self._watch:
            self._watch.join(timeout=2)
        self._status(SourceState.STOPPED, "Native-video capture stopped (extension keeps its badge until you press Stop)")

    def set_panel(self, panel: Rect | None) -> None:
        with self._lock:
            self._panel = panel
        if panel is None:
            self.server.send({"type": "set_panel", "rect": None})
        else:
            x, y, w, h = panel.to_int()
            self.server.send({"type": "set_panel", "rect": {"x": x, "y": y, "w": w, "h": h}})

    def request_overview(self) -> None:
        self.server.send({"type": "request_overview"})

    def set_overview_rate(self, hz: float) -> None:
        self.overview_hz = hz
        self.server.send({"type": "request_overview", "hz": hz})

    def request_in_page_selection(self) -> None:
        self.server.send({"type": "select-area"})

    # ------------------------------------------------------- server callbacks
    def _on_connect(self, cid: int) -> None:
        self.source_id = f"native:{cid}"
        self.server.send({"type": "config", "fps": self.fps, "max_in_flight": 2, "path": self.path})
        self._status(SourceState.RUNNING, "Extension connected", "")
        with self._lock:
            panel = self._panel
        if panel is not None:
            self.set_panel(panel)
        self.request_overview()

    def _on_disconnect(self, cid: int) -> None:
        self._status(SourceState.CLOSED, "Extension disconnected (tab closed, navigated, reloaded or stopped)",
                     "Activate the extension on the Twitch tab again, or use window capture")

    def _on_event(self, m: dict) -> None:
        t = m.get("type")
        if t == "meta":
            v = m.get("video") or {}
            if v.get("w") and v.get("h"):
                self.video_size = (int(v["w"]), int(v["h"]))
            c = m.get("colour") or {}
            if c:
                self.colour = f"{c.get('matrix')}/{c.get('transfer')}/{'full' if c.get('fullRange') else 'limited'}"
                if c.get("transfer") in ("pq", "hlg", "smpte2084", "arib-std-b67"):
                    self._status(SourceState.RUNNING, "HDR video detected: FaceFrame outputs SDR without tone "
                                 "mapping; colours may be wrong", "Use an SDR stream")
            if m.get("protectedMedia"):
                self._status(SourceState.FAILED, "Protected (DRM) video: pixels are not readable",
                             "Use window capture (Select Source)")
        elif t == "video-size-changed":
            old, new = m.get("old") or {}, m.get("video") or {}
            self.video_size = (int(new.get("w", 0)), int(new.get("h", 0)))
            self.gen = int(m.get("gen", self.gen))
            p = m.get("panel")
            details = {"old": (old.get("w"), old.get("h")), "new": self.video_size}
            if p:
                details["panel"] = Rect(p["x"], p["y"], p["w"], p["h"])
            self._status(SourceState.GEOMETRY_CHANGED,
                         f"Stream rendition changed {old.get('w')}x{old.get('h')} -> {new.get('w')}x{new.get('h')}",
                         "Webcam area rescaled proportionally; re-validating", details)
        elif t == "element-replaced":
            self.gen = int(m.get("gen", self.gen))
            self._status(SourceState.GEOMETRY_CHANGED, "Video element replaced by the page", "Re-validating",
                         {"element_replaced": True})
        elif t in ("element-lost", "navigated", "closed"):
            self._status(SourceState.CLOSED, f"Extension: {t} {m.get('reason') or m.get('href') or ''}".strip(),
                         "Activate the extension again on the Twitch tab")
        elif t == "blocked":
            self._status(SourceState.FAILED, f"Browser blocked pixel access: {m.get('error')}",
                         "Fall back to window capture (Select Source)")
        elif t == "hidden":
            self._status(SourceState.STALLED, "Tab hidden: Edge throttles video frames in background tabs",
                         "Keep the Twitch tab selected in a visible (not minimized) Edge window")
        elif t == "stalled":
            self._status(SourceState.STALLED, f"No video frames for {m.get('ms')} ms", "Is the stream paused or buffering?")
        elif t == "panel-selected":
            r = m.get("rect") or {}
            if self._sink is not None and hasattr(self._sink, "on_panel_selected_in_page"):
                self._sink.on_panel_selected_in_page(Rect(r.get("x", 0), r.get("y", 0), r.get("w", 0), r.get("h", 0)))
        elif t == "status":
            pass

    def _on_binary(self, hdr: FrameHeader, payload: memoryview, t_recv: int) -> None:
        sink = self._sink
        if sink is None:
            return
        self.video_size = (hdr.video_w, hdr.video_h)
        host_ns = self.server.clock.to_host_ns(hdr.t_present_ms)
        if host_ns is not None:
            t_cap, clock = host_ns, ClockDomain.BROWSER_CALIBRATED
            self.present_to_receive.add((t_recv - host_ns) / 1e6)
        else:
            t_cap, clock = t_recv, ClockDomain.HOST_RECEIVE
        bgr, conv = decode_pixels(hdr, payload)
        if hdr.msg_type == MSG_OVERVIEW:
            sink.on_overview(Overview(self.source_id, self.session_id, t_recv, (hdr.video_w, hdr.video_h), bgr,
                                      hdr.width / hdr.video_w, self.kind, (hdr.video_w, hdr.video_h)))
            return
        with self._lock:
            panel = self._panel
        if panel is None:
            return
        px, py, pw, ph = hdr.rect
        # frames for an older panel geometry (in flight during a change) are dropped
        x, y, w, h = panel.to_int()
        if abs(px - x) > 2 or abs(py - y) > 2 or abs(pw - w) > 2 or abs(ph - h) > 2:
            self.dropped_gen += 1
            return
        self.readback_ms.add(hdr.readback_ms)
        self.frames += 1
        self._last_frame_t = time.monotonic()
        self._seq += 1
        self.colour_conversion = conv
        f = Frame(self.source_id, self.session_id, self._seq, t_cap, clock, t_recv, self.kind,
                  (hdr.video_w, hdr.video_h), Rect(px, py, pw, ph), bgr, video_size=(hdr.video_w, hdr.video_h),
                  extra={"media_time": hdr.media_time, "gen": hdr.gen, "conversion": conv,
                         "readback_ms": hdr.readback_ms})
        self._last_frame_bgr = bgr
        sink.on_frame(f)
        if self.state != SourceState.RUNNING and self.state not in (SourceState.FAILED,):
            self._status(SourceState.RUNNING, "Receiving native video frames")
        # Pseudo-overview (panel pasted into a black video-sized canvas) for
        # content validation, so full frames need not be transported.
        now = time.monotonic()
        if self.overview_hz <= 0 and now - self._last_pseudo_ov > 0.5:
            self._last_pseudo_ov = now
            s = min(1.0, 640 / hdr.video_w)
            cw, ch = max(1, round(hdr.video_w * s)), max(1, round(hdr.video_h * s))
            canvas = np.zeros((ch, cw, 3), np.uint8)
            rx, ry = round(px * s), round(py * s)
            rw, rh = max(1, min(cw - rx, round(pw * s))), max(1, min(ch - ry, round(ph * s)))
            canvas[ry:ry + rh, rx:rx + rw] = cv2.resize(bgr, (rw, rh), interpolation=cv2.INTER_AREA)
            sink.on_overview(Overview(self.source_id, self.session_id, t_recv, (hdr.video_w, hdr.video_h), canvas, s,
                                      self.kind, (hdr.video_w, hdr.video_h)))

    def _watchdog(self) -> None:
        while not self._stop.wait(0.5):
            if self.state == SourceState.RUNNING and self._panel is not None and self._last_frame_t \
                    and time.monotonic() - self._last_frame_t > 2.0:
                self._status(SourceState.STALLED, "No native frames for 2 s", "Is the stream paused, or the tab hidden?")

    def _status(self, st: SourceState, msg: str, recovery: str = "", details: dict | None = None) -> None:
        if st != SourceState.GEOMETRY_CHANGED:
            self.state = st
        sink = self._sink
        if sink is not None:
            try:
                sink.on_status(SourceStatus(st, msg, recovery, details or {}))
            except Exception:
                log.exception("status sink failed")

    def stats(self) -> dict:
        p = self.present_to_receive.percentiles()
        r = self.readback_ms.percentiles()
        return {"native_frames": self.frames, "present_to_receive_p50": p[50], "present_to_receive_p95": p[95],
                "readback_p50": r[50], "readback_p95": r[95], "rx_fps": self.server.rx_rate.rate(),
                "rx_MBps": None, "clock_calibrated": self.server.clock.calibrated,
                "clock_rtt_ms": (self.server.clock.best() or (None,))[0], "dropped_old_geometry": self.dropped_gen,
                "malformed": self.server.malformed, "rejected_handshakes": self.server.rejected_handshakes,
                "rejected_tokens": self.server.rejected_tokens}
