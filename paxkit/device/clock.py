"""Clocks used by the reader. The real clock and a virtual test clock share one interface.

- `now()`: monotonic clock for computing waits (s).
- `sleep(dt)`: wait.
- `wall()`: PC time for receive timestamps (Unix s). Equivalent to PXSR `dayjs()` (= `Date.now()`, ms).
"""
from __future__ import annotations

import sys
import time


def _precise_wall_time():
    """On Windows `time.time()` ticks in 15.6 ms steps, too coarse for ms timestamps → read the precise system time directly."""
    if sys.platform != "win32":
        return time.time
    import ctypes

    ft = ctypes.c_ulonglong()
    get = ctypes.windll.kernel32.GetSystemTimePreciseAsFileTime

    def wall() -> float:
        get(ctypes.byref(ft))
        return ft.value / 1e7 - 11644473600.0   # 100 ns since 1601-01-01 → Unix s

    return wall


class RealClock:
    # Python 3.11+ `time.sleep` uses a high-resolution timer on Windows too (1 ms wait ≈ 1.1–1.7 ms measured).
    now = staticmethod(time.perf_counter)
    sleep = staticmethod(time.sleep)
    wall = staticmethod(_precise_wall_time())


class VirtualClock:
    """For tests. `sleep` does not actually wait; it only advances the time."""

    def __init__(self, wall_base: float = 0.0) -> None:
        self.t = 0.0
        self.wall_base = wall_base

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += max(dt, 0.0)

    def wall(self) -> float:
        return self.wall_base + self.t
