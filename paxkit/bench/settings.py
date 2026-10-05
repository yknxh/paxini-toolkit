"""게이지 테스트 설정 (`config.yaml`의 `bench:`, 계획 P6-4). 빠진 값은 아래 기본값."""
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
    "lag_correct": True,   # 세션마다 남은 게이지 지연을 상호상관으로 재서 짝짓기 전에 보정
    "lag_min_r": 0.8,      # 상관이 이보다 낮으면 보정하지 않음 (추정을 믿기 어려움)
    "simultaneous_ratio": 0.5,
    "hand_order": ["A1", "A2", "B1", "B2"],
    "rated_N": 25.0,   # %FS 기준 (config `sensor_types.*.rated_N`, 두 타입 모두 25 N)
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
    """커버리지 힘 구간 경계: 0 ~ max_N 을 3등분 (15 N이면 0~5 / 5~10 / 10~15, 마지막 구간은 그 위도 포함)."""
    m = float(s["max_N"])
    return [0.0, m / 3, 2 * m / 3]
