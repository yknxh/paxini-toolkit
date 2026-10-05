"""게이지 ↔ 센서 지연 추정 (paxtest `analysis/loader._xcorr_offset`에서 이전).

센서와 게이지는 같은 PC 시계로 수신 시각을 찍는다 (계획 2장 "하나의 시계"). 남는 것은 게이지 쪽 고정 지연뿐이고,
리더가 `latency_s`로 뺀다. 라이브 화면은 남은 지연을 표시만 하고, 게이지 테스트 분석은 세션마다 이 값으로 보정한다
(`bench/analyze.py`, 2026-10-05 사용자 요청). 기록 파일 값은 바꾸지 않는다.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


def xcorr_offset(tg, fg, tp, fp, t_lo: float, t_hi: float, max_off: float,
                 dt: float = 0.005) -> Tuple[Optional[float], float]:
    """sensor(t + lag) ≈ gauge(t)가 되는 lag(초)와 상관계수. lag > 0 이면 센서가 게이지보다 늦다."""
    grid = np.arange(t_lo, t_hi, dt)
    if len(grid) < 50:
        return None, 0.0
    g = np.interp(grid, tg, fg)
    n = int(max_off / dt)
    best, best_r = 0, -1.0
    gz = g - g.mean()
    if np.std(gz) < 1e-6:
        return None, 0.0
    for k in range(-n, n + 1, 2 if n > 400 else 1):
        # paxini(t + lag) ≈ gauge(t)
        p = np.interp(grid + k * dt, tp, fp)
        pz = p - p.mean()
        denom = np.linalg.norm(gz) * np.linalg.norm(pz)
        if denom <= 0:
            continue
        r = float(gz @ pz / denom)
        if r > best_r:
            best, best_r = k, r
    return best * dt, best_r


def sensor_lag(gauge_buffer, sensor_buffer, now: float, *, window: float = 10.0, max_off: float = 0.5,
               min_std_N: float = 0.3) -> Tuple[Optional[float], float]:
    """최근 window 초의 게이지 N과 센서 합력 크기(raw/10 N)로 센서 지연을 추정한다.

    게이지 변화가 min_std_N보다 작으면(누르지 않음) 추정하지 않는다 → (None, 0).
    """
    tg, vg = gauge_buffer.snapshot(since=now - window)
    tp, vp = sensor_buffer.snapshot(since=now - window)
    if len(tg) < 5 or len(tp) < 5:
        return None, 0.0
    fg = vg[:, 0]
    if np.std(fg) < min_std_N:
        return None, 0.0
    fp = np.linalg.norm(vp, axis=1) / 10.0
    lo = max(tg[0], tp[0]) + max_off
    hi = min(tg[-1], tp[-1]) - max_off
    return xcorr_offset(tg, fg, tp, fp, lo, hi, max_off)
