"""Selected-person tracking and time-based crop smoothing.

The tracker follows ONE user-selected face inside the selected webcam panel
using spatial continuity and detector confidence. It never switches to a face
outside its gate (a second person, a thumbnail, a game character); when the
target disappears it holds framing for a grace period, then reports LOST and
only reacquires a face that re-appears near the last known position.
No biometric identification is used or needed.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field

from .geometry import Rect


class TrackState(str, enum.Enum):
    SEARCHING = "SEARCHING"            # no person selected yet / after reset
    TRACKING = "TRACKING"              # selected face matched recently
    LOST = "LOST"                      # selected face not seen; holding / waiting
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"


@dataclass(frozen=True)
class Detection:
    box: Rect          # panel pixel coordinates (full resolution)
    score: float
    landmarks: tuple[tuple[float, float], ...] | None = None


@dataclass
class DetectionResult:
    session_id: int
    frame_seq: int
    t_frame_ns: int
    panel_size: tuple[int, int]
    detections: list[Detection]
    detect_ms: float = 0.0


@dataclass
class TrackerConfig:
    lost_after_s: float = 0.40       # unmatched this long -> LOST
    grace_s: float = 2.0             # LOST: keep last framing this long
    after_grace: str = "panel"       # "panel" | "neutral" | "hold"
    track_gate: float = 0.9          # face widths, while TRACKING
    reacquire_gate: float = 1.2      # face widths at start of LOST
    reacquire_growth: float = 0.6    # face widths per second while LOST
    reacquire_gate_max: float = 2.5
    min_score: float = 0.6
    box_alpha: float = 0.65          # EMA weight of a new matched detection


@dataclass
class FramingDecision:
    mode: str                 # "face" | "hold" | "panel" | "neutral"
    face: Rect | None
    state: TrackState
    reason: str = ""


class FaceTracker:
    def __init__(self, cfg: TrackerConfig | None = None) -> None:
        self.cfg = cfg or TrackerConfig()
        self.session_id = 0
        self.state = TrackState.SEARCHING
        self.target: Rect | None = None
        self.last_match_t: float | None = None
        self.lost_since_t: float | None = None
        self.last_seq = -1
        self.last_t_frame_ns = -1
        self.candidates: list[Detection] = []
        self.candidate_history: list[int] = []   # number of faces in recent results
        self._face_h_hist: list[float] = []       # largest face height per recent result
        self._dominance_hist: list[bool] = []     # one face clearly dominant in that result
        self.stale_rejected = 0
        self.panel_size: tuple[int, int] | None = None
        self._source_ok = True
        self.switch_guard_rejections = 0  # non-gated faces ignored while tracking

    # ----------------------------------------------------------------- control
    def reset(self, session_id: int | None = None) -> None:
        if session_id is not None:
            self.session_id = session_id
        self.state = TrackState.SEARCHING if self._source_ok else TrackState.SOURCE_UNAVAILABLE
        self.target = None
        self.last_match_t = None
        self.lost_since_t = None
        self.last_seq = -1
        self.last_t_frame_ns = -1
        self.candidates = []
        self.candidate_history = []
        self._face_h_hist = []
        self._dominance_hist = []

    def set_source_available(self, ok: bool, now_s: float) -> None:
        self._source_ok = ok
        if not ok:
            self.state = TrackState.SOURCE_UNAVAILABLE
        elif self.state == TrackState.SOURCE_UNAVAILABLE:
            # Back from an outage: we cannot assume the same person is in the
            # same place. Keep the target as a reacquisition hint only.
            if self.target is not None:
                self.state = TrackState.LOST
                self.lost_since_t = now_s
            else:
                self.state = TrackState.SEARCHING

    def select_at(self, px: float, py: float, now_s: float) -> Detection | None:
        """User clicked a point in panel coordinates: pick that face."""
        best, best_d = None, float("inf")
        for d in self.candidates:
            if d.box.contains_point(px, py):
                dist = 0.0
            else:
                dist = math.hypot(d.box.cx - px, d.box.cy - py) / max(d.box.w, 1.0)
            if dist < best_d:
                best, best_d = d, dist
        if best is None or best_d > 1.5:
            return None
        self.select_box(best.box, now_s)
        return best

    def select_box(self, box: Rect, now_s: float) -> None:
        self.target = box
        self.state = TrackState.TRACKING if self._source_ok else TrackState.SOURCE_UNAVAILABLE
        self.last_match_t = now_s
        self.lost_since_t = None

    def single_clear_candidate(self, min_results: int = 15, min_rel_height: float = 0.12) -> Detection | None:
        """Preselection hint: exactly one face in each of the last ``min_results``
        results (~1.5 s at 10-12 Hz), big enough to be the webcam subject (not a
        poster/thumbnail), and not much smaller than the largest face seen recently
        (so a background face seen while the streamer looks away is never chosen)."""
        hist = self._dominance_hist[-min_results:]
        # every recent result must contain one face that dominates all others (1.8x taller)
        if len(hist) < min_results or not all(hist) or not self.candidates:
            return None
        c = max(self.candidates, key=lambda d: d.box.h)
        if self.panel_size and c.box.h < min_rel_height * self.panel_size[1]:
            return None
        if self.recent_max_face_h and c.box.h < 0.6 * self.recent_max_face_h:
            return None
        return c

    @property
    def recent_max_face_h(self) -> float:
        return max(self._face_h_hist, default=0.0)

    # ----------------------------------------------------------------- update
    def update(self, res: DetectionResult, now_s: float) -> bool:
        """Apply a detection result. Returns False if rejected as stale."""
        if res.session_id != self.session_id or res.frame_seq <= self.last_seq \
                or res.t_frame_ns < self.last_t_frame_ns:
            self.stale_rejected += 1
            return False
        self.last_seq = res.frame_seq
        self.last_t_frame_ns = res.t_frame_ns
        self.panel_size = res.panel_size
        dets = [d for d in res.detections if d.score >= self.cfg.min_score]
        self.candidates = dets
        self.candidate_history.append(len(dets))
        if len(self.candidate_history) > 50:
            del self.candidate_history[:-50]
        hs = sorted((d.box.h for d in dets), reverse=True)
        self._face_h_hist.append(hs[0] if hs else 0.0)
        self._dominance_hist.append(bool(hs) and (len(hs) == 1 or hs[0] >= 1.8 * hs[1]))
        if len(self._dominance_hist) > 120:
            del self._dominance_hist[:-120]
        if len(self._face_h_hist) > 120:
            del self._face_h_hist[:-120]

        if self.state == TrackState.SOURCE_UNAVAILABLE or self.target is None:
            return True

        match = self._associate(dets, now_s)
        if match is not None:
            a = self.cfg.box_alpha
            t = self.target
            self.target = Rect(t.x + a * (match.box.x - t.x), t.y + a * (match.box.y - t.y),
                               t.w + a * (match.box.w - t.w), t.h + a * (match.box.h - t.h))
            self.last_match_t = now_s
            self.lost_since_t = None
            self.state = TrackState.TRACKING
        else:
            if len(dets) > 0:
                self.switch_guard_rejections += 1
            self.tick(now_s)
        return True

    def _gate(self, now_s: float) -> float:
        if self.state == TrackState.TRACKING:
            return self.cfg.track_gate
        lost_for = 0.0 if self.lost_since_t is None else max(0.0, now_s - self.lost_since_t)
        return min(self.cfg.reacquire_gate_max,
                   self.cfg.reacquire_gate + self.cfg.reacquire_growth * lost_for)

    def _associate(self, dets: list[Detection], now_s: float) -> Detection | None:
        t = self.target
        assert t is not None
        size = max(t.w, t.h, 1.0)
        gate = self._gate(now_s)
        lo, hi = (0.6, 1.6) if self.state == TrackState.TRACKING else (0.5, 2.0)
        best, best_score = None, -1.0
        for d in dets:
            dist = math.hypot(d.box.cx - t.cx, d.box.cy - t.cy) / size
            if dist > gate:
                continue
            ratio = d.box.h / max(t.h, 1.0)
            if not (lo <= ratio <= hi):
                continue
            s = t.iou(d.box) + 0.5 * (1.0 - dist / gate) + 0.2 * d.score
            if s > best_score:
                best, best_score = d, s
        return best

    def tick(self, now_s: float) -> None:
        """Advance timers when no matching detection arrives."""
        if self.state == TrackState.TRACKING and self.last_match_t is not None:
            if now_s - self.last_match_t > self.cfg.lost_after_s:
                self.state = TrackState.LOST
                self.lost_since_t = self.last_match_t

    # ----------------------------------------------------------------- output
    def decision(self, now_s: float) -> FramingDecision:
        self.tick(now_s)
        st = self.state
        if st == TrackState.SOURCE_UNAVAILABLE:
            return FramingDecision("neutral", None, st, "source unavailable")
        if st == TrackState.SEARCHING or self.target is None:
            return FramingDecision("panel", None, st, "no person selected")
        if st == TrackState.TRACKING:
            return FramingDecision("face", self.target, st)
        lost_for = now_s - (self.lost_since_t if self.lost_since_t is not None else now_s)
        if lost_for <= self.cfg.grace_s or self.cfg.after_grace == "hold":
            return FramingDecision("hold", self.target, st, f"lost {lost_for:.1f}s, holding")
        return FramingDecision(self.cfg.after_grace, None, st, f"lost {lost_for:.1f}s")


# --------------------------------------------------------------------------- #
# Crop smoothing
# --------------------------------------------------------------------------- #

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class SmootherParams:
    smoothness: float = 0.5          # 0 = responsive, 1 = very smooth
    dead_zone_pos: float = 0.035     # fraction of crop height
    dead_zone_zoom: float = 0.07     # log-size error
    max_speed: float | None = None   # crop heights per second (None -> from smoothness)
    max_zoom_rate: float = 0.35      # log-size per second

    @property
    def tau_pos(self) -> float:
        return _lerp(0.12, 1.0, self.smoothness)

    @property
    def tau_zoom(self) -> float:
        return 2.5 * self.tau_pos

    @property
    def speed(self) -> float:
        return self.max_speed if self.max_speed is not None else _lerp(1.6, 0.35, self.smoothness)


@dataclass
class CropSmoother:
    """Time-based critically-damped-ish follower with dead zone, bounded speed
    and slow zoom. Operates in panel pixel space; the caller clamps the result
    to the panel."""

    params: SmootherParams = field(default_factory=SmootherParams)
    cx: float | None = None
    cy: float | None = None
    w: float | None = None
    h: float | None = None
    _moving: bool = False
    _zooming: bool = False

    def snap(self, r: Rect) -> Rect:
        self.cx, self.cy, self.w, self.h = r.cx, r.cy, r.w, r.h
        self._moving = self._zooming = False
        return r

    @property
    def initialized(self) -> bool:
        return self.cx is not None

    def current(self) -> Rect | None:
        if self.cx is None:
            return None
        return Rect(self.cx - self.w / 2, self.cy - self.h / 2, self.w, self.h)

    def update(self, target: Rect, dt: float) -> Rect:
        if self.cx is None or dt <= 0:
            if self.cx is None:
                return self.snap(target)
            return self.current()  # type: ignore[return-value]
        dt = min(dt, 0.25)  # a stall must not cause a jump
        p = self.params

        # --- zoom (log space, slower than translation)
        ez_w = math.log(max(target.w, 1e-3) / self.w)
        ez_h = math.log(max(target.h, 1e-3) / self.h)
        ez = max(abs(ez_w), abs(ez_h))
        if ez > p.dead_zone_zoom:
            self._zooming = True
        elif ez < p.dead_zone_zoom * 0.25:
            self._zooming = False
        if self._zooming:
            k = 1.0 - math.exp(-dt / p.tau_zoom)
            lim = p.max_zoom_rate * dt
            self.w *= math.exp(max(-lim, min(lim, ez_w * k)))
            self.h *= math.exp(max(-lim, min(lim, ez_h * k)))

        # --- translation
        ex, ey = target.cx - self.cx, target.cy - self.cy
        err = math.hypot(ex, ey) / max(self.h, 1.0)
        if err > p.dead_zone_pos:
            self._moving = True
        elif err < p.dead_zone_pos * 0.25:
            self._moving = False
        if self._moving:
            k = 1.0 - math.exp(-dt / p.tau_pos)
            sx, sy = ex * k, ey * k
            step = math.hypot(sx, sy)
            max_step = p.speed * self.h * dt
            if step > max_step > 0:
                sx, sy = sx * max_step / step, sy * max_step / step
            self.cx += sx
            self.cy += sy
        return self.current()  # type: ignore[return-value]
