"""리더가 쓰는 시계. 실제 시계와 테스트용 가상 시계를 같은 인터페이스로 둔다.

- `now()`: 대기 시간 계산용 단조 시계 (초).
- `sleep(dt)`: 대기.
- `wall()`: 수신 시각 기록용 PC 시각 (Unix 초). PXSR `dayjs()`(= `Date.now()`, ms)에 해당.
"""
from __future__ import annotations

import sys
import time


def _precise_wall_time():
    """Windows의 `time.time()`은 15.6 ms 단위라 ms 시각을 못 쓴다 → 정밀 시스템 시각을 직접 읽는다."""
    if sys.platform != "win32":
        return time.time
    import ctypes

    ft = ctypes.c_ulonglong()
    get = ctypes.windll.kernel32.GetSystemTimePreciseAsFileTime

    def wall() -> float:
        get(ctypes.byref(ft))
        return ft.value / 1e7 - 11644473600.0   # 1601-01-01 기준 100 ns → Unix 초

    return wall


class RealClock:
    # Python 3.11+ `time.sleep`은 Windows에서도 고해상도 타이머를 쓴다 (1 ms 대기 ≈ 1.1~1.7 ms 실측).
    now = staticmethod(time.perf_counter)
    sleep = staticmethod(time.sleep)
    wall = staticmethod(_precise_wall_time())


class VirtualClock:
    """테스트용. `sleep`이 실제로 기다리지 않고 시각만 앞으로 옮긴다."""

    def __init__(self, wall_base: float = 0.0) -> None:
        self.t = 0.0
        self.wall_base = wall_base

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += max(dt, 0.0)

    def wall(self) -> float:
        return self.wall_base + self.t
