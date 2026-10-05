"""Pairing on gauge timestamps and sample classification (plan P6-2 2~5). Coverage during recording and post-analysis share these functions.

- The reference is the gauge sample (~10 Hz, the slower side). Sensor resultant force is linearly interpolated between the
  surrounding frames (dropped if their gap > max_gap_s); taxels take the nearest frame.
- No lag correction here (sensor and gauge share the same PC clock).
- Compared quantity: sensor resultant magnitude |F| = √(X² + Y² + Z²) [N] (raw × 0.1) vs gauge [N]. Error = |F| − gauge.
- Contact: gauge ≥ contact_N. Stable: in contact, and the least-squares slopes of gauge and sensor |F| within ± stable_window_s
  are both ≤ stable_slope_N_per_s. No-load: gauge < noload_N with the same slope condition (released and held still).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from .zones import NO_ZONE, ZoneSet

RAW_TO_N = 0.1


@dataclass
class SensorSeries:
    """Frame series of one sensor (post-analysis: from the CSV, during recording: from the sink)."""
    t: np.ndarray           # (n,) Unix seconds
    force_raw: np.ndarray   # (n, 3) resultant raw X, Y, Z
    taxel_z: np.ndarray     # (n, N) taxel Z raw

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, dtype=float)
        self.force_raw = np.asarray(self.force_raw, dtype=float).reshape(len(self.t), 3)
        self.taxel_z = np.asarray(self.taxel_z, dtype=float).reshape(len(self.t), -1)


def window_slopes(t: np.ndarray, v: np.ndarray, at: np.ndarray, half: float, min_n: int) -> np.ndarray:
    """Least-squares slope of (t, v) within [at − half, at + half] for each at. NaN if fewer than min_n points."""
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
    """Sensor resultant N (n, 3) interpolated at each gauge time + nearest frame index. NaN / -1 if unpaired."""
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
    """Pairs sensor values with each gauge sample and classifies them. Returns column name → array (length = number of gauge samples).

    Rows with `paired` False (no sensor pair) have NaN F values and contact/stable/noload all False.
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
    # no-load: only segments with the hand off and still (transitions right before pressing / after release are timing offsets, not sensor residual)
    noload = paired & (gv < float(settings["noload_N"])) & (np.abs(slope_g) <= lim) & (np.abs(slope_s) <= lim)

    n = len(gt)
    zone = np.full(n, NO_ZONE)
    cop = np.full((n, 3), np.nan)
    if zones is not None and s.taxel_z.shape[1] == zones.geometry.n_taxels:
        has = near >= 0
        tz = s.taxel_z[near[has]]
        zone[has] = zones.classify(tz, float(settings["min_taxel_sum"]))
        cop[has] = zones.geometry.cop(tz)
        cop[zone == NO_ZONE] = np.nan   # no position (taxel Z sum too small)
    return {
        "t_unix_s": gt, "gauge_N": gv,
        "Fx_N": F[:, 0], "Fy_N": F[:, 1], "Fz_N": F[:, 2], "F_N": mag,
        "error_N": mag - gv, "error_z_N": F[:, 2] - gv,
        "paired": paired, "contact": contact, "stable": stable, "noload": noload,
        "slope_gauge": slope_g, "slope_sensor": slope_s,
        "zone": zone, "cop_x_mm": cop[:, 0], "cop_y_mm": cop[:, 1], "cop_z_mm": cop[:, 2],
    }
