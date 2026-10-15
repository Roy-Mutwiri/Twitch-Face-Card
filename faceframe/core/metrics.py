"""Lightweight rolling metrics (rates, latency percentiles)."""

from __future__ import annotations

import threading
import time
from collections import deque


class RateCounter:
    """Events per second over a sliding window."""

    def __init__(self, window_s: float = 2.0) -> None:
        self.window_s = window_s
        self._t: deque[float] = deque()
        self._lock = threading.Lock()
        self.total = 0

    def tick(self, t: float | None = None) -> None:
        t = time.perf_counter() if t is None else t
        with self._lock:
            self._t.append(t)
            self.total += 1
            self._trim(t)

    def _trim(self, now: float) -> None:
        while self._t and now - self._t[0] > self.window_s:
            self._t.popleft()

    def rate(self) -> float:
        now = time.perf_counter()
        with self._lock:
            self._trim(now)
            n = len(self._t)
            if n < 2:
                return 0.0
            # span to *now* so the rate decays when events stop arriving
            span = max(now - self._t[0], self._t[-1] - self._t[0], 1e-6)
            return (n - 1) / span


class LatencyStats:
    """Keeps the last N samples (ms) for percentiles."""

    def __init__(self, maxlen: int = 600) -> None:
        self._s: deque[float] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.count = 0

    def add(self, ms: float) -> None:
        with self._lock:
            self._s.append(ms)
            self.count += 1

    def percentiles(self, ps=(50, 95, 99)) -> dict[int, float]:
        with self._lock:
            data = sorted(self._s)
        if not data:
            return {p: float("nan") for p in ps}
        out = {}
        for p in ps:
            k = min(len(data) - 1, max(0, int(round(p / 100.0 * (len(data) - 1)))))
            out[p] = data[k]
        return out

    def mean(self) -> float:
        with self._lock:
            return sum(self._s) / len(self._s) if self._s else float("nan")

    def last(self) -> float:
        with self._lock:
            return self._s[-1] if self._s else float("nan")


class Ema:
    def __init__(self, alpha: float = 0.1) -> None:
        self.alpha = alpha
        self.value: float | None = None

    def add(self, v: float) -> float:
        self.value = v if self.value is None else self.value + self.alpha * (v - self.value)
        return self.value
