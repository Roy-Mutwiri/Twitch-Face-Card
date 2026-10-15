"""Webcam-panel geometry validation.

The panel rectangle is only meaningful while the page/stream layout is the one
the user selected it on. This module keeps a recent grayscale reference of the
panel (taken from the low-rate overview) and, on each new overview:

* follows small pure translations (page scroll) when the match is strong;
* after a source resize, searches scale+position (the Twitch player scales
  the video uniformly) and accepts only a strong, unambiguous match;
* declares the panel INVALID after sustained mismatch (ad break, streamer
  scene change, different tab, layout change) -> the app shows a neutral frame
  and asks for reselection; it automatically becomes VALID again if the last
  good reference re-appears at the same place (e.g. after an ad).

It never guesses: anything below the thresholds is INVALID, never "close enough".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import Rect


class PanelValidity(str, enum.Enum):
    VALID = "VALID"
    CHECKING = "CHECKING"
    INVALID = "INVALID"


@dataclass
class ValidatorConfig:
    follow_score: float = 0.70     # accept translation / refresh reference
    relocate_score: float = 0.82   # accept after resize (scale search)
    invalid_score: float = 0.45    # below this counts as a miss
    misses_to_invalid: int = 3
    hits_to_valid: int = 2
    search_margin: float = 0.12    # fraction of panel size for translation search
    ref_long_side: int = 128


def _gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _match(search: np.ndarray, tmpl: np.ndarray) -> tuple[float, tuple[int, int]]:
    if search.shape[0] < tmpl.shape[0] or search.shape[1] < tmpl.shape[1] or tmpl.size < 64:
        return -1.0, (0, 0)
    if float(tmpl.std()) < 2.0:
        # Flat template (black screen): correlation undefined -> treat as no match.
        return -1.0, (0, 0)
    res = cv2.matchTemplate(search, tmpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(res)
    return float(mx), loc


def ring_score(region: np.ndarray, tmpl: np.ndarray, frac: float = 0.2) -> float:
    """Correlation over the outer ring of the panel only. The person usually
    occupies the centre; an ad, scene switch or wrong layout replaces the
    background/frame too, a person moving or leaving does not."""
    if region.shape != tmpl.shape or tmpl.size < 64:
        return -1.0
    h, w = tmpl.shape
    m = np.ones((h, w), bool)
    m[int(h * frac):h - int(h * frac), int(w * frac):w - int(w * frac)] = False
    a = region[m].astype(np.float64)
    b = tmpl[m].astype(np.float64)
    if a.std() < 2.0 or b.std() < 2.0:
        return -1.0
    return float(np.corrcoef(a, b)[0, 1])


class PanelValidator:
    def __init__(self, cfg: ValidatorConfig | None = None) -> None:
        self.cfg = cfg or ValidatorConfig()
        self.reset()

    def reset(self) -> None:
        self.state = PanelValidity.CHECKING
        self.ref: np.ndarray | None = None          # gray, overview scale
        self.ref_scale: float = 1.0                  # overview px per source px when taken
        self.ref_source_size: tuple[int, int] | None = None
        self.misses = 0
        self.hits = 0
        self.last_score = float("nan")
        self._reloc_candidate: Rect | None = None

    def set_reference(self, overview_bgr: np.ndarray, ov_scale: float, panel: Rect,
                      source_size: tuple[int, int]) -> None:
        g = _gray(overview_bgr)
        r = panel.scaled(ov_scale)
        x, y, w, h = r.to_int()
        x, y = max(0, x), max(0, y)
        tmpl = g[y:y + h, x:x + w]
        if tmpl.size < 64:
            return
        self.ref = tmpl.copy()
        self.ref_scale = ov_scale
        self.ref_source_size = source_size
        self.state = PanelValidity.VALID
        self.misses = self.hits = 0

    def preload_reference(self, ref_gray: np.ndarray, ref_scale: float, source_size: tuple[int, int]) -> None:
        """Reference saved in an earlier session: stays CHECKING until it matches."""
        self.ref = ref_gray.copy()
        self.ref_scale = ref_scale
        self.ref_source_size = tuple(source_size)
        self.state = PanelValidity.CHECKING
        self.misses = self.hits = 0

    def reference_image(self) -> np.ndarray | None:
        return None if self.ref is None else self.ref.copy()

    def check(self, overview_bgr: np.ndarray, ov_scale: float, panel: Rect,
              source_size: tuple[int, int]) -> tuple[PanelValidity, Rect, float]:
        """Validate ``panel`` (source px) against a new overview.
        Returns (state, possibly-updated panel, score)."""
        if self.ref is None:
            self.set_reference(overview_bgr, ov_scale, panel, source_size)
            return self.state, panel, 1.0
        g = _gray(overview_bgr)
        if self.ref_source_size != source_size:
            return self._relocate(g, ov_scale, panel, source_size)

        tmpl = self.ref
        if abs(ov_scale - self.ref_scale) > 1e-6:
            f = ov_scale / self.ref_scale
            tmpl = cv2.resize(tmpl, (max(1, round(tmpl.shape[1] * f)), max(1, round(tmpl.shape[0] * f))),
                              interpolation=cv2.INTER_AREA)
        r = panel.scaled(ov_scale)
        mx = int(r.w * self.cfg.search_margin) + 2
        my = int(r.h * self.cfg.search_margin) + 2
        x0, y0 = max(0, int(r.x) - mx), max(0, int(r.y) - my)
        x1 = min(g.shape[1], int(r.x2) + mx + 1)
        y1 = min(g.shape[0], int(r.y2) + my + 1)
        score, loc = _match(g[y0:y1, x0:x1], tmpl)
        if score < self.cfg.follow_score and score > -1.0:
            # whole panel changed a lot: decide on the background ring instead
            th, tw = tmpl.shape
            reg = g[y0 + loc[1]:y0 + loc[1] + th, x0 + loc[0]:x0 + loc[0] + tw]
            score = max(score, ring_score(reg, tmpl))
        self.last_score = score
        new_panel = panel
        if score >= self.cfg.follow_score:
            nx, ny = (x0 + loc[0]) / ov_scale, (y0 + loc[1]) / ov_scale
            if abs(nx - panel.x) > 1.5 / ov_scale or abs(ny - panel.y) > 1.5 / ov_scale:
                new_panel = Rect(nx, ny, panel.w, panel.h)
            self._hit()
            if self.state == PanelValidity.VALID and score >= 0.85:
                # refresh reference to follow gradual content changes (only on strong
                # whole-panel matches, so a person leaving does not erode the reference)
                self.set_reference(overview_bgr, ov_scale, new_panel, source_size)
        elif score < self.cfg.invalid_score:
            # Large pure translation (page scroll / layout shift)? Search the whole
            # overview at the same scale; accept only a strong match seen twice.
            gscore, gloc = _match(g, tmpl)
            if gscore >= self.cfg.relocate_score:
                cand = Rect(gloc[0] / ov_scale, gloc[1] / ov_scale, panel.w, panel.h)
                prev = self._reloc_candidate
                if prev is not None and prev.iou(cand) >= 0.9:
                    self._reloc_candidate = None
                    self.set_reference(overview_bgr, ov_scale, cand, source_size)
                    return self.state, cand, gscore
                self._reloc_candidate = cand
                self.state = PanelValidity.CHECKING
                return self.state, panel, score
            self._reloc_candidate = None
            self._miss()
        return self.state, new_panel, score

    def _relocate(self, g, ov_scale, panel, source_size):
        """Source surface size changed: search scale + position."""
        assert self.ref is not None
        best = (-1.0, None)
        # template size in the new overview = ref size * (layout scale) * ov_scale/ref_scale
        base = ov_scale / self.ref_scale
        for k in np.geomspace(0.35, 2.8, 48):
            f = base * k
            tw, th = round(self.ref.shape[1] * f), round(self.ref.shape[0] * f)
            if tw < 12 or th < 12 or tw >= g.shape[1] or th >= g.shape[0]:
                continue
            t = cv2.resize(self.ref, (tw, th), interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR)
            score, loc = _match(g, t)
            if score > best[0]:
                best = (score, (loc[0], loc[1], tw, th))
        score, box = best
        self.last_score = score
        if box is not None and score >= self.cfg.relocate_score:
            x, y, w, h = box
            new_panel = Rect(x / ov_scale, y / ov_scale, w / ov_scale, h / ov_scale)
            prev = self._reloc_candidate
            if prev is None or prev.iou(new_panel) < 0.9:
                # first sighting: wait for a second, independent overview to agree
                self._reloc_candidate = new_panel
                self.state = PanelValidity.CHECKING
                return self.state, panel, score
            self._reloc_candidate = None
            # re-take the reference at the new layout scale (also marks VALID)
            self.set_reference(g, ov_scale, new_panel, source_size)
            return self.state, new_panel, score
        self._reloc_candidate = None
        self.misses += 1
        self.state = PanelValidity.CHECKING if self.misses < self.cfg.misses_to_invalid else PanelValidity.INVALID
        return self.state, panel, score

    def _hit(self) -> None:
        self.misses = 0
        self.hits += 1
        if self.state != PanelValidity.VALID and self.hits >= self.cfg.hits_to_valid:
            self.state = PanelValidity.VALID

    def _miss(self) -> None:
        self.hits = 0
        self.misses += 1
        if self.misses >= self.cfg.misses_to_invalid:
            self.state = PanelValidity.INVALID
