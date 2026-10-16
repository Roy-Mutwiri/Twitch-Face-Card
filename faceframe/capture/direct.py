"""Direct Twitch stream source: highest available rendition, decoded by FFmpeg.

Streamlink (BSD-2) only resolves the HLS playlist URL for the chosen quality
(e.g. 1080p60); FFmpeg (via PyAV) fetches and decodes it. Every frame is the
full decoded rendition (e.g. 1920x1080) no matter how big the Edge player is,
whether Edge is covered, minimized or even closed.

Not synchronised with the Edge tab you hear: both are Twitch HLS players with
their own delay. A user-set extra delay (seconds) holds frames back so video
can be lined up with the audio you route to LIVE Studio.

Colour: Twitch does not tag the matrix (FFmpeg reports "unspecified"); HD
H.264 is BT.709 limited range, so the panel is converted from the decoder's
YUV planes with BT.709 (not swscale's BT.601 default).
"""

from __future__ import annotations

import collections
import logging
import threading
import time

import cv2
import numpy as np

from ..core.frame import ClockDomain, Frame, Overview, SourceKind, now_ns
from ..core.geometry import Rect
from ..core.source_base import SourceSink, SourceState, SourceStatus
from .yuv import yuv420_to_bgr

log = logging.getLogger("faceframe.direct")


def normalize_target(text: str) -> tuple[str, str]:
    """Channel name or any twitch.tv URL (live channel or VOD, optionally ?t=1h2m3s)
    -> (url, short label)."""
    t = text.strip()
    if not t:
        raise ValueError("enter a Twitch channel name or a twitch.tv URL")
    if "twitch.tv/" in t:
        url = t if t.startswith("http") else "https://" + t.lstrip("/")
        path = url.split("twitch.tv/", 1)[1].split("?")[0].strip("/")
        if not path:
            raise ValueError(f"no channel or video in {text!r}")
        return url, path.replace("/", ":")
    if not t.replace("_", "").isalnum():
        raise ValueError(f"not a channel name: {text!r}")
    return f"https://www.twitch.tv/{t.lower()}", t.lower()


def list_qualities(target: str) -> list[str]:
    import streamlink
    url, _ = normalize_target(target)
    return [q for q in streamlink.Streamlink().streams(url) if q not in ("worst",)]


def _height(name: str) -> int:
    """'1080p60' -> 1080, 'audio_only' -> 0."""
    try:
        return int(str(name).split("p")[0])
    except ValueError:
        return 0


def _rank(name: str) -> tuple[int, int]:
    try:
        h, f = name.split("p")
        return int(h), int(f or 30)
    except ValueError:
        return 0, 0


def _hms_param(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600}h{sec % 3600 // 60}m{sec % 60}s"


def parse_hms(text: str) -> float | None:
    """'1:02:03', '62:03', '1h2m3s', '2m', '3723' -> seconds; None if unreadable."""
    import re
    t = (text or "").strip().lower().replace(" ", "")
    if not t:
        return None
    if any(c in t for c in "hms"):
        m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
        if m and any(m.groups()):
            h, mi, se = (int(x or 0) for x in m.groups())
            return float(h * 3600 + mi * 60 + se)
        return None
    parts = t.split(":")
    if all(x.isdigit() for x in parts) and 1 <= len(parts) <= 3:
        v = 0
        for x in parts:
            v = v * 60 + int(x)
        return float(v)
    return None


class DirectStreamSource:
    kind = SourceKind.DIRECT_STREAM

    def __init__(self, channel: str, quality: str = "best", delay_s: float = 0.0, session_id: int = 0) -> None:
        self.url, self.channel = normalize_target(channel)
        self.is_vod = "/videos/" in self.url
        self.quality = quality
        self.delay_s = max(0.0, float(delay_s))
        self.session_id = session_id
        self.source_id = f"direct:{self.channel}"
        self._sink: SourceSink | None = None
        self._panel: Rect | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._queue: collections.deque = collections.deque()
        self._qcond = threading.Condition()
        self._container = None
        self.overview_hz = 2.0
        self._last_ov = 0.0
        self._ov_requested = True
        self.video_size: tuple[int, int] | None = None
        self.rendition = ""
        self.fps_nominal = 0.0
        self.decoded = 0
        self.dropped_late = 0
        self.state = SourceState.IDLE
        self._seq = 0
        self.max_fps = 60.0            # panel conversion rate cap (set to the output rate)
        self.rebuffers = 0
        self.startup_cushion_s = 1.0
        # shared playback clock (video schedule + audio position): (base_pts, base_wall)
        self._clk_lock = threading.Lock()
        self._base: tuple[float, float] | None = None
        self._paused_at: float | None = None
        self.paused = False
        self._reconnect = False
        # audio
        self._audio: collections.deque = collections.deque()   # (pts, float32 (n, 2))
        self._audio_cursor: float | None = None
        self._audio_err = 0.0
        self._resampler = None
        self.audio_stream = None
        self.volume = 1.0
        self.muted = False
        self.audio_resyncs = 0
        self.audio_requested = 0       # samples asked for while playing
        self.audio_served = 0          # samples taken from decoded audio (rest = silence gaps)
        # time display
        self.duration_s: float | None = None     # VOD total length
        self.segment_start_s: float = 0.0        # VOD position of the first played segment
        self._first_pts: float | None = None
        self._watch_started: float | None = None
        self._paused_total = 0.0
        self.added_latency_s = 0.0
        self._last_conv_due = -1e9
        self._announced = False
        # VOD seeking: position to (re)open at, and decoded video before it is discarded
        self._seek_s: float | None = None
        self._seek_pending = False
        self._skip_until_rel: float = 0.0        # seconds after the first decoded frame
        # performance: highest rendition height allowed for "best" (None = no cap)
        self.max_height: int | None = None

    # ------------------------------------------------------------- control
    def describe(self) -> dict:
        return {"kind": self.kind.value, "channel": self.channel, "quality": self.rendition or self.quality,
                "video_size": self.video_size, "delay_s": self.delay_s,
                "note": "Decoded Twitch HLS rendition (not the Edge player); own timing, see delay."}

    def set_panel(self, panel: Rect | None) -> None:
        with self._lock:
            self._panel = panel
        with self._qcond:
            self._queue.clear()  # buffered frames were cut for the old panel

    def request_overview(self) -> None:
        self._ov_requested = True

    def set_overview_rate(self, hz: float) -> None:
        self.overview_hz = max(0.5, float(hz))

    def set_delay(self, s: float) -> None:
        self.delay_s = max(0.0, min(30.0, float(s)))

    def seek(self, position_s: float) -> bool:
        """VOD only: continue playback at ``position_s`` (seconds from the VOD start)."""
        if not self.is_vod:
            return False
        pos = max(0.0, float(position_s))
        if self.duration_s:
            pos = min(pos, max(0.0, self.duration_s - 1.0))
        now = time.perf_counter()
        with self._clk_lock:
            if self._paused_at is not None:          # seeking also resumes
                self._paused_total += now - self._paused_at
                self._paused_at = None
                self.paused = False
            self._seek_s = pos
            self._seek_pending = True
            self._reconnect = True
        with self._qcond:
            self._queue.clear()
            self._qcond.notify_all()
        log.info("seek to %.1f s", pos)
        return True

    def set_max_height(self, h: int | None) -> None:
        """Cap the rendition used for "best" (performance). Reopens the stream when the
        current rendition no longer matches (VODs continue at the same position)."""
        h = int(h) if h else None
        if h == self.max_height:
            return
        self.max_height = h
        cur = _height(self.rendition)
        if self.quality == "best" and cur and ((h is not None and cur > h) or h is None or cur < h):
            self._reconnect = True
            with self._qcond:
                self._qcond.notify_all()

    def start(self, sink: SourceSink) -> None:
        self._sink = sink
        self._stop.clear()
        self._threads = [threading.Thread(target=self._decode_loop, name="direct-decode", daemon=True),
                         threading.Thread(target=self._present_loop, name="direct-present", daemon=True)]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        with self._qcond:
            self._queue.clear()
            self._qcond.notify_all()
        # Never close the FFmpeg container from this thread (closing it while the decode
        # thread is inside decode() crashes natively). Closing the relay socket instead
        # makes FFmpeg see end-of-stream at once; the decode thread then closes it.
        self._close_relay()
        for t in self._threads:
            t.join(timeout=16)
        self._status(SourceState.STOPPED, "Direct stream stopped; network/decoder released")

    # ------------------------------------------------------------- decoding
    def _open(self):
        """Streamlink fetches the HLS stream (worker-thread segment prefetch, Twitch
        low-latency, ad segments skipped); its byte stream is relayed to FFmpeg over a
        private loopback socket. Measured: FFmpeg's own HLS reader delivered bursts with
        multi-second gaps at the live edge; this path delivers a steady 60 fps."""
        import socket

        import av
        import streamlink
        from streamlink.options import Options
        self._status(SourceState.STARTING, f"Resolving {self.url} …")
        session = streamlink.Streamlink()
        url = self.url
        if self.is_vod and self._seek_s is not None:
            url = self.url.split("?")[0] + "?t=" + _hms_param(self._seek_s)
        streams = session.streams(url, options=Options({"low-latency": not self.is_vod}))
        if not streams:
            raise RuntimeError(f"{self.url} is offline or has no public stream")
        names = [n for n in streams if n not in ("audio_only", "worst", "best")]
        q = self.quality if self.quality in streams else "best"
        if q == "best":
            best_url = streams["best"].url
            q = next((n for n in names if streams[n].url == best_url), "best")
            if self.max_height and _height(q) > self.max_height:
                fit = [n for n in names if 0 < _height(n) <= self.max_height]
                if fit:
                    q = max(fit, key=_rank)
        self.rendition = q
        if self.is_vod:
            self._vod_timing(session, streams[q])
        self._status(SourceState.STARTING, f"Opening {q} stream …")
        fd = streams[q].open()
        head = fd.read(4096)
        if not head:
            fd.close()
            raise RuntimeError("stream returned no data")
        fmt = "mpegts" if head[0] == 0x47 else "mp4"   # Twitch serves TS or fragmented MP4 (CMAF)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(15)
        port = srv.getsockname()[1]
        relay = {"fd": fd, "srv": srv, "conn": None}
        self._relay = relay

        def pump():
            conn = None
            try:
                conn, _ = srv.accept()
                relay["conn"] = conn
                conn.sendall(head)
                while not self._stop.is_set():
                    d = fd.read(65536)
                    if not d:
                        break
                    conn.sendall(d)
            except Exception as e:  # socket closed on stop, or network error
                log.debug("relay ended: %s", e)
            finally:
                for x in (conn, srv):
                    try:
                        x and x.close()
                    except Exception:
                        pass
                try:
                    fd.close()
                except Exception:
                    pass

        threading.Thread(target=pump, name="direct-relay", daemon=True).start()
        c = av.open(f"tcp://127.0.0.1:{port}", format=fmt, timeout=(15, 15))
        v = c.streams.video[0]
        v.thread_type = "AUTO"
        self.fps_nominal = float(v.average_rate or 0)
        self.container_format = fmt
        self.audio_stream = c.streams.audio[0] if c.streams.audio else None
        return c, v, names

    def _vod_timing(self, session, stream) -> None:
        """Total VOD length and the VOD position where playback starts (start of the
        segment containing the ?t= offset) from the media playlist."""
        import re
        try:
            durs = [float(x) for x in re.findall(r"#EXTINF:([0-9.]+)", session.http.get(stream.url).text)]
            self.duration_s = sum(durs)
            off = float(getattr(stream, "start_offset", 0) or 0)
            acc = 0.0
            for d in durs:
                if acc + d > off:
                    break
                acc += d
            self.segment_start_s = acc
            # playback starts at the segment boundary; decoded video before ``off`` is skipped
            self._skip_until_rel = max(0.0, off - acc)
        except Exception as e:
            log.info("VOD timing unavailable: %s", e)

    def _close_relay(self) -> None:
        r = getattr(self, "_relay", None)
        if r:
            for k in ("conn", "srv"):
                try:
                    r[k] and r[k].close()
                except Exception:
                    pass
            try:
                r["fd"].close()
            except Exception:
                pass

    # ---------------------------------------------------------------- clock
    def due(self, pts: float) -> float:
        """Wall time (perf_counter) at which media time ``pts`` is presented."""
        with self._clk_lock:
            if self._base is None or self._paused_at is not None:
                return float("inf")
            return self._base[1] + (pts - self._base[0]) + self.delay_s

    def media_time(self, wall: float | None = None) -> float | None:
        """Media time (pts) being presented at ``wall``."""
        wall = time.perf_counter() if wall is None else wall
        with self._clk_lock:
            if self._base is None:
                return None
            if self._paused_at is not None:
                wall = self._paused_at
            return self._base[0] + (wall - self._base[1] - self.delay_s)

    def pause(self) -> None:
        with self._clk_lock:
            if self._paused_at is None:
                self._paused_at = time.perf_counter()
                self.paused = True

    def resume(self) -> None:
        with self._clk_lock:
            if self._paused_at is None:
                return
            dur = time.perf_counter() - self._paused_at
            if not self.is_vod and dur > 8.0:
                # a live stream cannot be held for long (Twitch's playlist window moves on):
                # jump back to the live edge. Flagged BEFORE unpausing so the decode thread
                # (waiting in backpressure, not in I/O) drops the old connection itself and
                # reopens. The relay is not closed here: that could hit the new connection.
                self._reconnect = True
            self._paused_at = None
            self.paused = False
            self._paused_total += dur
            if self._base is not None:
                self._base = (self._base[0], self._base[1] + dur)
        with self._qcond:
            self._qcond.notify_all()

    def timing(self) -> dict:
        """Position / length / remaining for the UI (seconds)."""
        mt = self.media_time()
        if self.is_vod:
            pos = None if (mt is None or self._first_pts is None) else self.segment_start_s + (mt - self._first_pts)
            if pos is not None and self.duration_s is not None:
                pos = min(max(0.0, pos), self.duration_s)
            rem = None if (pos is None or self.duration_s is None) else max(0.0, self.duration_s - pos)
            return {"live": False, "position": pos, "duration": self.duration_s, "remaining": rem,
                    "paused": self.paused}
        watched = None
        if self._watch_started is not None:
            end = self._paused_at if self._paused_at is not None else time.perf_counter()
            watched = max(0.0, end - self._watch_started - self._paused_total)
        return {"live": True, "position": watched, "duration": None, "remaining": None, "paused": self.paused}

    # ---------------------------------------------------------------- audio
    def _push_audio(self, frame) -> None:
        import av
        if frame.pts is None:
            return
        if self._resampler is None:
            self._resampler = av.AudioResampler(format="flt", layout="stereo", rate=48000)
        pts = float(frame.pts * frame.time_base)
        if self._first_pts is not None and pts - self._first_pts < self._skip_until_rel - 0.1:
            return                                       # audio before the requested VOD second
        for rf in self._resampler.resample(frame):
            a = rf.to_ndarray().reshape(-1, 2).astype(np.float32, copy=False)
            with self._clk_lock:
                self._audio.append((pts, a))
                while self._audio and pts - self._audio[0][0] > self.delay_s + 12.0:
                    self._audio.popleft()
            pts += a.shape[0] / 48000.0

    def read_audio(self, n: int, dac_wall: float) -> np.ndarray:
        """The samples that should be heard at ``dac_wall`` (called from the audio callback).

        The read cursor free-runs; callback timing jitter (GIL, scheduling) is filtered out
        with a smoothed error and small drift is corrected by playing 0.4 % faster/slower
        (inaudible). Only a real clock jump (> 250 ms: delay change, re-anchor) resyncs hard."""
        out = np.zeros((n, 2), np.float32)
        if self.muted or self.paused:
            return out
        target = self.media_time(dac_wall)
        if target is None:
            return out
        with self._clk_lock:
            if self._audio_cursor is None or abs(self._audio_cursor - target) > 0.25:
                if self._audio_cursor is not None:
                    self.audio_resyncs += 1
                self._audio_cursor = target
                self._audio_err = 0.0
            cur = self._audio_cursor
            self._audio_err += 0.05 * ((target - cur) - self._audio_err)
            k = 0
            if abs(self._audio_err) > 0.012:
                k = max(1, int(n * 0.004)) * (1 if self._audio_err > 0 else -1)
            m = n + k
            while self._audio and self._audio[0][0] + self._audio[0][1].shape[0] / 48000.0 <= cur:
                self._audio.popleft()
            src = np.zeros((m, 2), np.float32)
            filled = 0
            for pts, a in self._audio:
                if filled >= m:
                    break
                off = int(round((cur + filled / 48000.0 - pts) * 48000.0))
                if off < 0:
                    filled += min(m - filled, -off)   # gap before this chunk: silence
                    off = 0
                if off >= a.shape[0] or filled >= m:
                    continue
                take = min(m - filled, a.shape[0] - off)
                src[filled:filled + take] = a[off:off + take]
                filled += take
            if k == 0:
                out = src
            else:
                xi = np.linspace(0.0, m - 1, n)
                for ch in range(2):
                    out[:, ch] = np.interp(xi, np.arange(m), src[:, ch])
            self._audio_cursor = cur + m / 48000.0
            self.audio_requested += m
            self.audio_served += min(filled, m)
        if self.volume != 1.0:
            out *= self.volume
            np.clip(out, -1.0, 1.0, out=out)
        return out

    # -------------------------------------------------------------- decoding
    def _decode_loop(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            try:
                if self.is_vod and not self._seek_pending and self._first_pts is not None:
                    pos = self.timing().get("position")      # reconnect / rendition change: continue here
                    if pos is not None:
                        self._seek_s = pos
                self._seek_pending = False
                self._reconnect = False
                self._skip_until_rel = 0.0
                c, v, names = self._open()
                self._container = c
                self._announced = False      # RUNNING is reported on the first frame of every (re)connect
                backoff = 2.0
                with self._clk_lock:
                    self._base = None
                    self._audio.clear()
                    self._audio_cursor = None
                self._first_pts = None
                self._resampler = None
                if self._watch_started is None:
                    self._watch_started = time.perf_counter()
                streams = [v] + ([self.audio_stream] if self.audio_stream is not None else [])
                for pkt in c.demux(*streams):
                    if self._stop.is_set() or self._reconnect:
                        break
                    if pkt.dts is None:
                        continue
                    for f in pkt.decode():
                        if f.pts is None:
                            continue
                        if pkt.stream.type == "audio":
                            self._push_audio(f)
                            continue
                        pts = float(f.pts * f.time_base)
                        now = time.perf_counter()
                        if self._first_pts is None:
                            self._first_pts = pts
                        if pts - self._first_pts < self._skip_until_rel:
                            continue                     # before the requested VOD second
                        with self._clk_lock:
                            b = self._base
                            if self._paused_at is None and (
                                    b is None or abs((b[1] + pts - b[0]) - now) > 8.0 + self.delay_s):
                                # (re)anchor at start / discontinuity (e.g. ad splice) with a
                                # startup cushion so early live-segment jitter does not underrun
                                self._base = (pts, now + self.startup_cushion_s)
                        due = self.due(pts)
                        if due < now - 0.05:
                            # adaptive playout: the network made us late. Shift the shared clock
                            # back once (lateness + cushion); audio follows the same clock.
                            shift = (now - due) + 0.3
                            with self._clk_lock:
                                if self._base is not None:
                                    self._base = (self._base[0], self._base[1] + shift)
                            self.rebuffers += 1
                            self.added_latency_s += shift
                        item = self._prepare(f, pts)
                        with self._qcond:
                            if item is not None:
                                self.decoded += 1
                                self._queue.append(item)
                                self._qcond.notify_all()
                            # backpressure on EVERY decoded frame (queued or not): stay at most
                            # delay + 4 s ahead of the clock. Without it a VOD (downloads faster
                            # than real time) races ahead and the clock re-anchors (time jumps).
                            # While paused this blocks, so the stream is not read any further.
                            while not self._stop.is_set() and not self._reconnect and (
                                    self.paused or self.due(pts) - time.perf_counter() > self.delay_s + 4.0):
                                self._qcond.wait(0.05)
                if self._reconnect and not self._stop.is_set():
                    with self._qcond:
                        self._queue.clear()
                    continue
                if not self._stop.is_set():
                    if self.is_vod:
                        self._status(SourceState.CLOSED, "VOD finished", "Pick another source")
                        return
                    raise RuntimeError("stream ended (offline or interrupted)")
            except Exception as e:
                if self._stop.is_set():
                    break
                if self._reconnect:
                    continue
                log.warning("direct stream: %s", e)
                self._status(SourceState.STALLED, f"Twitch stream unavailable: {e}",
                             f"Retrying in {backoff:.0f} s (is the channel live?)")
                self._stop.wait(backoff)
                backoff = min(30.0, backoff * 2)
            finally:
                self._close_relay()
                c, self._container = self._container, None
                if c is not None:
                    try:
                        c.close()
                    except Exception:
                        pass

    def _prepare(self, f, pts: float):
        """Cut the panel from the decoder's YUV planes (BT.709) and, at a low rate,
        a downscaled overview. Returns (pts, panel_bgr|None, overview|None, size)."""
        w, h = f.width, f.height
        if self.video_size != (w, h):
            old = self.video_size
            self.video_size = (w, h)
            if old is not None:
                with self._lock:
                    p = self._panel
                det = {}
                if p is not None and old[0] > 0:
                    k = w / old[0]
                    det["panel"] = Rect(p.x * k, p.y * k, p.w * k, p.h * k)
                self._status(SourceState.GEOMETRY_CHANGED, f"Rendition size {old[0]}x{old[1]} -> {w}x{h}",
                             "Re-validating", det)
        if not self._announced:
            self._announced = True
            self._status(SourceState.RUNNING, f"Decoding {self.rendition} {w}x{h}")
        if f.format.name not in ("yuv420p", "yuvj420p"):
            f = f.reformat(format="yuv420p")
        full = f.format.name == "yuvj420p"
        yp = np.frombuffer(f.planes[0], np.uint8).reshape(-1, f.planes[0].line_size)[:h, :w]
        up = np.frombuffer(f.planes[1], np.uint8).reshape(-1, f.planes[1].line_size)[:h // 2, :w // 2]
        vp = np.frombuffer(f.planes[2], np.uint8).reshape(-1, f.planes[2].line_size)[:h // 2, :w // 2]
        matrix = "bt709" if h >= 720 else "bt601"
        with self._lock:
            panel = self._panel
        panel_bgr = None
        convert = pts - self._last_conv_due >= 0.9 / max(1.0, self.max_fps) or pts < self._last_conv_due
        if panel is not None and convert:
            self._last_conv_due = pts
            x, y, pw, ph = panel.to_int()
            x, y = max(0, x) & ~1, max(0, y) & ~1
            pw, ph = min(pw, w - x) & ~1, min(ph, h - y) & ~1
            if pw >= 2 and ph >= 2:
                panel_bgr = (yuv420_to_bgr(yp[y:y + ph, x:x + pw], up[y // 2:(y + ph) // 2, x // 2:(x + pw) // 2],
                                           vp[y // 2:(y + ph) // 2, x // 2:(x + pw) // 2], matrix, full),
                             Rect(x, y, pw, ph))
        ov = None
        now = time.perf_counter()
        if self._ov_requested or now - self._last_ov >= 1.0 / self.overview_hz:
            self._ov_requested = False
            self._last_ov = now
            s = min(1.0, 960 / w)
            ow, oh = int(w * s) & ~1, int(h * s) & ~1
            ov = (yuv420_to_bgr(cv2.resize(yp, (ow, oh), interpolation=cv2.INTER_AREA),
                                cv2.resize(up, (ow // 2, oh // 2), interpolation=cv2.INTER_AREA),
                                cv2.resize(vp, (ow // 2, oh // 2), interpolation=cv2.INTER_AREA), matrix, full), s)
        if panel_bgr is None and ov is None:
            return None
        return (pts, panel_bgr, ov, (w, h))

    # ------------------------------------------------------------ presenting
    def _present_loop(self) -> None:
        while not self._stop.is_set():
            with self._qcond:
                while not self._stop.is_set() and not self._queue:
                    self._qcond.wait(0.1)
                if self._stop.is_set():
                    return
                pts, panel, ov, size = self._queue[0]
                wait = self.due(pts) - time.perf_counter()   # inf while paused
                if wait > 0:
                    self._qcond.wait(min(wait, 0.05))
                    continue
                self._queue.popleft()
                self._qcond.notify_all()
                late = -wait
            if late > 0.25 and panel is not None and self._queue:
                self.dropped_late += 1   # behind schedule: skip instead of building latency
                continue
            sink = self._sink
            if sink is None:
                continue
            t = now_ns()
            if ov is not None:
                sink.on_overview(Overview(self.source_id, self.session_id, t, size, ov[0], ov[1], self.kind, size))
            if panel is not None:
                self._seq += 1
                sink.on_frame(Frame(self.source_id, self.session_id, self._seq, t, ClockDomain.HOST_PERF, t,
                                    self.kind, size, panel[1], panel[0], video_size=size,
                                    extra={"rendition": self.rendition}))

    def _status(self, st: SourceState, msg: str, recovery: str = "", details: dict | None = None) -> None:
        if st != SourceState.GEOMETRY_CHANGED:
            self.state = st
        sink = self._sink
        if sink is not None:
            try:
                sink.on_status(SourceStatus(st, msg, recovery, details or {}))
            except Exception:
                log.exception("status sink failed")
