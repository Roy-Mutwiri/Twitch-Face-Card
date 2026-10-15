"""Source interface used by the pipeline (GUI-free)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Protocol

from .frame import Frame, Overview
from .geometry import Rect


class SourceState(str, enum.Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STALLED = "STALLED"            # no new frames (paused video, occluded/throttled)
    MINIMIZED = "MINIMIZED"        # window minimized: WGC delivers no frames
    GEOMETRY_CHANGED = "GEOMETRY_CHANGED"
    WAITING = "WAITING"            # bridge waiting for the extension
    CLOSED = "CLOSED"              # source window/tab went away
    FAILED = "FAILED"
    STOPPED = "STOPPED"


AVAILABLE_STATES = {SourceState.RUNNING, SourceState.STALLED, SourceState.GEOMETRY_CHANGED}


@dataclass
class SourceStatus:
    state: SourceState
    message: str = ""
    recovery: str = ""
    details: dict = field(default_factory=dict)


class SourceSink(Protocol):
    def on_frame(self, frame: Frame) -> None: ...
    def on_overview(self, ov: Overview) -> None: ...
    def on_status(self, status: SourceStatus) -> None: ...


class Source(Protocol):
    source_id: str
    session_id: int

    def start(self, sink: SourceSink) -> None: ...
    def stop(self) -> None: ...
    def set_panel(self, panel: Rect | None) -> None: ...
    def request_overview(self) -> None: ...
    def describe(self) -> dict: ...
