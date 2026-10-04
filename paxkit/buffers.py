"""스레드 안전한 시계열 링 버퍼 (라이브 표시용). paxtest `devices/buffers.py`에서 이전."""
from __future__ import annotations

import threading
from collections import deque
from typing import Optional, Sequence, Tuple

import numpy as np


class TimeSeriesBuffer:
    def __init__(self, maxlen: int = 20000, ncols: int = 1):
        self.ncols = ncols
        self._t: deque = deque(maxlen=maxlen)
        self._v: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, t: float, v: Sequence[float] | float) -> None:
        with self._lock:
            self._t.append(t)
            self._v.append(v)

    def clear(self) -> None:
        with self._lock:
            self._t.clear()
            self._v.clear()

    def snapshot(self, since: Optional[float] = None) -> Tuple[np.ndarray, np.ndarray]:
        with self._lock:
            t = np.array(self._t, dtype=float)
            v = np.array(self._v, dtype=float)
        if v.size == 0:
            return np.empty(0), np.empty((0, self.ncols))
        v = v.reshape(len(t), self.ncols)
        if since is not None:
            idx = np.searchsorted(t, since)
            t, v = t[idx:], v[idx:]
        return t, v

    def latest(self) -> Tuple[Optional[float], Optional[np.ndarray]]:
        with self._lock:
            if not self._t:
                return None, None
            return self._t[-1], np.atleast_1d(np.array(self._v[-1], dtype=float))

    def rate(self, now: float, window: float = 2.0) -> float:
        with self._lock:
            if len(self._t) < 2:
                return 0.0
            n = 0
            for t in reversed(self._t):
                if t < now - window:
                    break
                n += 1
        return n / window
