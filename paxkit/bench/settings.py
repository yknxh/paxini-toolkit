"""Gauge test settings (`bench:` in `config.yaml`, plan P6-4). Missing values fall back to the defaults below."""
from __future__ import annotations

from typing import Any, Dict, Optional

DEFAULTS: Dict[str, Any] = {
    "max_N": 15.0,
    "contact_N": 0.5,
    "noload_N": 0.2,
    "noload_warn_N": 0.3,
    "noload_check_s": 3.0,
    "stable_window_s": 0.2,
    "stable_slope_N_per_s": 2.0,
    "max_gap_s": 0.1,
    "gauge_max_N": 100.0,
    "min_taxel_sum": 5.0,
    "bin_N": 1.0,
    "zone_min_samples": 20,
    "lag_warn_s": 0.1,
    "lag_correct": True,   # measure the remaining gauge lag per session by cross-correlation and correct it before pairing
    "lag_min_r": 0.8,      # no correction if the correlation is below this (estimate unreliable)
    "simultaneous_ratio": 0.5,
    "hand_order": ["A1", "A2", "B1", "B2"],
    "rated_N": 25.0,   # %FS reference (config `sensor_types.*.rated_N`, 25 N for both types)
}


def bench_settings(cfg=None, override: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    if cfg is not None:
        rated = {v.get("rated_N") for v in (cfg.section("sensor_types") or {}).values() if isinstance(v, dict)}
        if len(rated) == 1 and None not in rated:
            out["rated_N"] = float(rated.pop())
        out.update(cfg.section("bench"))
    if override:
        out.update(override)
    return out


def force_bins(s: Dict[str, Any]):
    """Coverage force-bin edges: 0 ~ max_N split into thirds (15 N → 0~5 / 5~10 / 10~15; the last bin also covers values above)."""
    m = float(s["max_N"])
    return [0.0, m / 3, 2 * m / 3]
