"""Gauge ↔ sensor lag estimation (ported from paxtest `analysis/loader._xcorr_offset`).

Sensor and gauge receive times are stamped with the same PC clock (plan ch. 2 "one clock"). Only a fixed gauge-side latency
remains, which the reader subtracts via `latency_s`. The live view only displays the remaining lag; the gauge test analysis
corrects it per session (`bench/analyze.py`, 2026-10-05 user request). Recorded file values are not changed.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


def xcorr_offset(tg, fg, tp, fp, t_lo: float, t_hi: float, max_off: float,
                 dt: float = 0.005) -> Tuple[Optional[float], float]:
    """lag (s) such that sensor(t + lag) ≈ gauge(t), and the correlation coefficient. lag > 0 means the sensor lags the gauge."""
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
    """Estimates sensor lag from gauge N and sensor resultant magnitude (raw/10 N) over the last window seconds.

    No estimate if the gauge variation is below min_std_N (not pressed) → (None, 0).
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
