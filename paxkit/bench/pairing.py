"""게이지 시각 기준 짝짓기·샘플 분류 (계획 P6-2 2~5). 기록 중 커버리지와 사후 분석이 같은 함수를 쓴다.

- 기준은 게이지 샘플 (약 10 Hz, 느린 쪽). 센서 합력은 앞뒤 프레임 선형 보간 (앞뒤 간격 > max_gap_s면 버림),
  taxel은 가장 가까운 프레임 값.
- 지연 보정은 하지 않는다 (센서·게이지가 같은 PC 시계).
- 비교량: 센서 합력 크기 |F| = √(X² + Y² + Z²) [N] (raw × 0.1) 대 게이지 [N]. 오차 = |F| − 게이지.
- 접촉: 게이지 ≥ contact_N. 안정: 접촉이고 ± stable_window_s 안에서 게이지·센서 |F|의 기울기(최소제곱)가
  모두 ≤ stable_slope_N_per_s. 무부하: 게이지 < noload_N 이고 같은 기울기 조건 (뗀 채 가만히 있는 구간).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from .zones import NO_ZONE, ZoneSet

RAW_TO_N = 0.1


@dataclass
class SensorSeries:
    """센서 하나의 프레임 열 (사후: CSV에서, 기록 중: sink에서)."""
    t: np.ndarray           # (n,) Unix 초
    force_raw: np.ndarray   # (n, 3) 합력 raw X, Y, Z
    taxel_z: np.ndarray     # (n, N) taxel Z raw

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, dtype=float)
        self.force_raw = np.asarray(self.force_raw, dtype=float).reshape(len(self.t), 3)
        self.taxel_z = np.asarray(self.taxel_z, dtype=float).reshape(len(self.t), -1)


def window_slopes(t: np.ndarray, v: np.ndarray, at: np.ndarray, half: float, min_n: int) -> np.ndarray:
    """at 마다 [at − half, at + half] 안의 (t, v) 최소제곱 기울기. 점이 min_n개 미만이면 NaN."""
    ok = np.isfinite(v)
    t, v = t[ok], v[ok]
    out = np.full(len(at), np.nan)
    if len(t) < min_n or len(at) == 0:
        return out
    t0 = t[0]
    x = t - t0
    cs = lambda a: np.concatenate([[0.0], np.cumsum(a)])
    S1, Sx, Sy, Sxx, Sxy = cs(np.ones_like(x)), cs(x), cs(v), cs(x * x), cs(x * v)
    lo = np.searchsorted(t, at - half, side="left")
    hi = np.searchsorted(t, at + half, side="right")
    n = S1[hi] - S1[lo]
    sx, sy, sxx, sxy = Sx[hi] - Sx[lo], Sy[hi] - Sy[lo], Sxx[hi] - Sxx[lo], Sxy[hi] - Sxy[lo]
    den = n * sxx - sx * sx
    with np.errstate(invalid="ignore", divide="ignore"):
        s = (n * sxy - sx * sy) / den
    good = (n >= min_n) & (den > 1e-12)
    out[good] = s[good]
    return out


def interp_force(gt: np.ndarray, s: SensorSeries, max_gap: float):
    """게이지 시각마다 센서 합력 N (n, 3) 보간 + 가장 가까운 프레임 번호. 짝이 없으면 NaN / -1."""
    n = len(gt)
    F = np.full((n, 3), np.nan)
    near = np.full(n, -1)
    if len(s.t) < 2 or n == 0:
        return F, near
    j = np.searchsorted(s.t, gt, side="left")
    exact = (j < len(s.t)) & (s.t[np.minimum(j, len(s.t) - 1)] == gt)
    inside = (j > 0) & (j < len(s.t))
    jj = np.clip(j, 1, len(s.t) - 1)
    t0, t1 = s.t[jj - 1], s.t[jj]
    ok = inside & ((t1 - t0) <= max_gap)
    w = np.where(t1 > t0, (gt - t0) / np.where(t1 > t0, t1 - t0, 1.0), 0.0)
    f0, f1 = s.force_raw[jj - 1], s.force_raw[jj]
    Fi = (f0 + (f1 - f0) * w[:, None]) * RAW_TO_N
    F[ok] = Fi[ok]
    je = np.minimum(j, len(s.t) - 1)
    F[exact] = s.force_raw[je[exact]] * RAW_TO_N
    ok = ok | exact
    near_i = np.where(np.abs(gt - t0) <= np.abs(t1 - gt), jj - 1, jj)
    near_i = np.where(exact, je, near_i)
    near[ok] = near_i[ok]
    return F, near


def pair_sensor(gt: np.ndarray, gv: np.ndarray, s: SensorSeries, settings: Dict,
                zones: Optional[ZoneSet] = None) -> Dict[str, np.ndarray]:
    """게이지 샘플마다 센서 값을 짝짓고 분류한다. 반환은 열 이름 → 배열 (게이지 샘플 수 길이).

    `paired`가 False인 행(센서 짝 없음)은 F 값이 NaN이고 contact/stable/noload 모두 False.
    """
    gt = np.asarray(gt, dtype=float)
    gv = np.asarray(gv, dtype=float)
    F, near = interp_force(gt, s, float(settings["max_gap_s"]))
    mag = np.sqrt((F ** 2).sum(axis=1))
    paired = np.isfinite(mag)
    half = float(settings["stable_window_s"])
    slope_g = window_slopes(gt, gv, gt, half, 3)
    smag = np.sqrt(((s.force_raw * RAW_TO_N) ** 2).sum(axis=1))
    slope_s = window_slopes(s.t, smag, gt, half, 3)
    lim = float(settings["stable_slope_N_per_s"])
    contact = paired & (gv >= float(settings["contact_N"]))
    stable = contact & (np.abs(slope_g) <= lim) & (np.abs(slope_s) <= lim)
    # 무부하: 손을 뗀 채 가만히 있는 구간만 (누르기 직전·뗀 직후의 변화 구간은 센서 잔류가 아니라 시각 차이)
    noload = paired & (gv < float(settings["noload_N"])) & (np.abs(slope_g) <= lim) & (np.abs(slope_s) <= lim)

    n = len(gt)
    zone = np.full(n, NO_ZONE)
    cop = np.full((n, 3), np.nan)
    if zones is not None and s.taxel_z.shape[1] == zones.geometry.n_taxels:
        has = near >= 0
        tz = s.taxel_z[near[has]]
        zone[has] = zones.classify(tz, float(settings["min_taxel_sum"]))
        cop[has] = zones.geometry.cop(tz)
        cop[zone == NO_ZONE] = np.nan   # 위치 없음 (taxel Z 합이 작음)
    return {
        "t_unix_s": gt, "gauge_N": gv,
        "Fx_N": F[:, 0], "Fy_N": F[:, 1], "Fz_N": F[:, 2], "F_N": mag,
        "error_N": mag - gv, "error_z_N": F[:, 2] - gv,
        "paired": paired, "contact": contact, "stable": stable, "noload": noload,
        "slope_gauge": slope_g, "slope_sensor": slope_s,
        "zone": zone, "cop_x_mm": cop[:, 0], "cop_y_mm": cop[:, 1], "cop_z_mm": cop[:, 2],
    }
