"""Bounded latest-value mailboxes. A producer never blocks; an unread item that
is superseded is dropped and counted. Consumers wait for something newer than
what they already saw, so work never piles up into seconds of latency."""

from __future__ import annotations

import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class LatestSlot(Generic[T]):
    """Capacity-1 queue keeping only the newest item."""

    def __init__(self, name: str = "slot") -> None:
        self.name = name
        self._cond = threading.Condition()
        self._item: T | None = None
        self._version = 0
        self._closed = False
        self.puts = 0
        self.dropped = 0  # items replaced before any consumer took them
        self._taken_version = 0

    def put(self, item: T) -> None:
        with self._cond:
            if self._item is not None and self._taken_version < self._version:
                self.dropped += 1
            self._item = item
            self._version += 1
            self.puts += 1
            self._cond.notify_all()

    def get_newer(self, seen_version: int, timeout: float | None = None) -> tuple[T | None, int]:
        """Block until an item newer than ``seen_version`` exists (or timeout/close).
        Returns (item, version); item is None on timeout/close."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._closed or self._version > seen_version, timeout):
                return None, seen_version
            if self._closed and self._version <= seen_version:
                return None, seen_version
            self._taken_version = self._version
            return self._item, self._version

    def peek(self) -> tuple[T | None, int]:
        with self._cond:
            return self._item, self._version

    def clear(self) -> None:
        """Flush (used on source change). Waiters keep waiting for a new item."""
        with self._cond:
            self._item = None
            self._taken_version = self._version

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def reopen(self) -> None:
        with self._cond:
            self._closed = False

    @property
    def version(self) -> int:
        with self._cond:
            return self._version

    @property
    def pending(self) -> int:
        """0 or 1 — the bounded depth of this queue."""
        with self._cond:
            return 1 if (self._item is not None and self._taken_version < self._version) else 0
