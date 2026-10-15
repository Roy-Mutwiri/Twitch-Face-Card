"""Frame contract shared by every pipeline stage.

Every frame that leaves a source is an *owned*, contiguous copy. Native capture
buffers (WGC mapped textures, WebSocket receive buffers) are never retained past
the callback that delivered them.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field

import numpy as np

from .geometry import Rect


class SourceKind(str, enum.Enum):
    # Pixels composited by DWM for an Edge window (Windows Graphics Capture).
    # These are *rendered window pixels*, never decoder pixels.
    WGC_RENDERED = "wgc-rendered-window"
    # Pixels read from the <video> element's decoded frame by the extension.
    NATIVE_VIDEO = "native-video-element"
    # Twitch HLS rendition fetched and decoded by FaceFrame itself (FFmpeg).
    DIRECT_STREAM = "direct-hls-decoded"
    # Synthetic test patterns / clips.
    TEST = "test"


class ClockDomain(str, enum.Enum):
    # time.perf_counter_ns() in this process.
    HOST_PERF = "host-perf-ns"
    # Browser timestamp converted to host perf via a measured ping/pong offset.
    BROWSER_CALIBRATED = "browser-calibrated-to-host-perf"
    # Host receive time only (browser clock not calibrated yet).
    HOST_RECEIVE = "host-receive-perf-ns"


def now_ns() -> int:
    return time.perf_counter_ns()


@dataclass
class Frame:
    """A single panel (webcam inset) image plus everything needed to interpret it.

    pixels       H x W x 3 uint8 BGR, C-contiguous, owned by this object.
    source_size  (w, h) of the full surface the panel was cut from: the
                 captured window for WGC, videoWidth/videoHeight for native.
    panel_rect   where ``pixels`` lie inside that surface, in surface pixels.
    """

    source_id: str
    session_id: int
    seq: int
    t_capture_ns: int
    clock: ClockDomain
    t_receive_ns: int
    source_kind: SourceKind
    source_size: tuple[int, int]
    panel_rect: Rect
    pixels: np.ndarray
    pixel_format: str = "BGR8"
    owned: bool = True
    # Decoded video resolution, only when actually measured (native bridge).
    video_size: tuple[int, int] | None = None
    extra: dict = field(default_factory=dict)

    @property
    def stride(self) -> int:
        return int(self.pixels.strides[0])

    @property
    def size(self) -> tuple[int, int]:
        return int(self.pixels.shape[1]), int(self.pixels.shape[0])

    def age_ms(self, at_ns: int | None = None) -> float:
        return ((at_ns or now_ns()) - self.t_capture_ns) / 1e6


@dataclass
class Overview:
    """Low-rate, downscaled full-surface image used only for selection UIs."""

    source_id: str
    session_id: int
    t_ns: int
    source_size: tuple[int, int]
    pixels: np.ndarray  # BGR, already downscaled
    scale: float  # overview px = source px * scale
    source_kind: SourceKind
    video_size: tuple[int, int] | None = None
