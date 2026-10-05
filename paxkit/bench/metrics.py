"""Error metrics (plan P6-2 6). Reports stable-sample metrics together with metrics over all contact samples (bias·SD·RMSE)
(2026-10-06 user decision: plot all contact samples, colored stable/unstable). Z-only values are for reference.

No pass/fail judgment (2026-10-05 user decision) — numbers only.
The linear fit |F| = a·gauge + b and R² are only reported, never applied to the data.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

# metrics.csv column order (after scope, id, name)
COLUMNS = [
    "n", "enough", "gauge_min_N", "gauge_max_N", "bias_N", "sd_N", "rmse_N", "mae_N", "max_abs_N", "p95_abs_N",
    "rmse_pct_fs", "slope", "intercept", "r2",
    "n_contact", "bias_contact_N", "sd_contact_N", "rmse_contact_N",
    "bias_z_N", "rmse_z_N",
    "n_noload", "noload_mean_N", "noload_max_N",
]


def _r(x: Optional[float], nd: int = 6) -> Optional[float]:
    """Value for result files: NaN → None, others rounded (deterministic re-analysis, readability)."""
    if x is None:
        return None
    x = float(x)
    return None if not np.isfinite(x) else round(x, nd)


def error_metrics(gauge: np.ndarray, F: np.ndarray, rated_N: float) -> Dict[str, Optional[float]]:
    """Gauge N, sensor |F| N → bias·SD·RMSE etc. With no samples, n=0 and the rest None."""
    g = np.asarray(gauge, dtype=float)
    f = np.asarray(F, dtype=float)
    ok = np.isfinite(g) & np.isfinite(f)
    g, f = g[ok], f[ok]
    n = len(g)
    out: Dict[str, Optional[float]] = {k: None for k in (
        "gauge_min_N", "gauge_max_N", "bias_N", "sd_N", "rmse_N", "mae_N", "max_abs_N", "p95_abs_N",
        "rmse_pct_fs", "slope", "intercept", "r2")}
    out["n"] = n
    if n == 0:
        return out
    e = f - g
    a = np.abs(e)
    rmse = float(np.sqrt(np.mean(e * e)))
    out.update(gauge_min_N=_r(g.min()), gauge_max_N=_r(g.max()), bias_N=_r(e.mean()),
               sd_N=_r(e.std(ddof=1)) if n > 1 else None, rmse_N=_r(rmse), mae_N=_r(a.mean()),
               max_abs_N=_r(a.max()), p95_abs_N=_r(np.percentile(a, 95)),
               rmse_pct_fs=_r(100.0 * rmse / rated_N))
    if n >= 3 and np.ptp(g) > 1e-9:
        slope, icpt = np.polyfit(g, f, 1)
        pred = slope * g + icpt
        ss_res = float(((f - pred) ** 2).sum())
        ss_tot = float(((f - f.mean()) ** 2).sum())
        out.update(slope=_r(slope), intercept=_r(icpt), r2=_r(1 - ss_res / ss_tot) if ss_tot > 0 else None)
    return out


def group_metrics(cols: Dict[str, np.ndarray], mask: np.ndarray, settings: Dict,
                  noload_mask: Optional[np.ndarray] = None) -> Dict[str, Optional[float]]:
    """Metrics for one group (overall, zone, sensor). mask = samples in this group."""
    rated = float(settings["rated_N"])
    st = mask & cols["stable"]
    ct = mask & cols["contact"]
    m = error_metrics(cols["gauge_N"][st], cols["F_N"][st], rated)
    m["enough"] = bool(m["n"] >= int(settings["zone_min_samples"]))
    c = error_metrics(cols["gauge_N"][ct], cols["F_N"][ct], rated)
    m.update(n_contact=c["n"], bias_contact_N=c["bias_N"], sd_contact_N=c["sd_N"], rmse_contact_N=c["rmse_N"])
    z = error_metrics(cols["gauge_N"][st], cols["Fz_N"][st], rated)
    m.update(bias_z_N=z["bias_N"], rmse_z_N=z["rmse_N"])
    if noload_mask is not None:
        r = cols["F_N"][noload_mask & np.isfinite(cols["F_N"])]
        m.update(n_noload=int(len(r)), noload_mean_N=_r(r.mean()) if len(r) else None,
                 noload_max_N=_r(r.max()) if len(r) else None)
    else:
        m.update(n_noload=None, noload_mean_N=None, noload_max_N=None)
    return {k: m.get(k) for k in COLUMNS}


def binned(gauge: np.ndarray, err: np.ndarray, bin_N: float, min_n: int = 3):
    """Error plot band: (center, mean, SD, n) per gauge bin of width bin_N. Bins with fewer than min_n samples are dropped."""
    g = np.asarray(gauge, dtype=float)
    e = np.asarray(err, dtype=float)
    ok = np.isfinite(g) & np.isfinite(e)
    g, e = g[ok], e[ok]
    if len(g) == 0:
        return np.empty(0), np.empty(0), np.empty(0), np.empty(0, dtype=int)
    k = np.floor(g / bin_N).astype(int)
    centers, means, sds, ns = [], [], [], []
    for b in np.unique(k):
        sel = e[k == b]
        if len(sel) < min_n:
            continue
        centers.append((b + 0.5) * bin_N)
        means.append(sel.mean())
        sds.append(sel.std(ddof=1))
        ns.append(len(sel))
    return np.array(centers), np.array(means), np.array(sds), np.array(ns, dtype=int)
