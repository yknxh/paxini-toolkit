"""기록 중 커버리지 집계 (계획 P6-1 3): 구역별 안정 샘플 수와 힘 구간 채움.

센서 sink(프레임)·게이지 sink(샘플)로 받은 값을 모아 두고, `update()` 때 짝짓기 판정에 필요한 앞뒤 데이터가
다 모인 게이지 샘플만 사후 분석과 같은 함수(`pair_sensor`)로 분류한다. 그래서 화면 숫자는 분석 결과와 같은 규칙이다
(분석은 게이지 이상값 제외 등이 더 있어 숫자가 조금 다를 수 있음).
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

import numpy as np

from .pairing import RAW_TO_N, SensorSeries, pair_sensor
from .settings import force_bins
from .zones import NO_ZONE, ZoneSet

KEEP_S = 3.0   # 처리한 뒤에도 남겨 두는 과거 데이터 (기울기 창·보간용)


class Coverage:
    def __init__(self, zones: Optional[ZoneSet], settings: Dict) -> None:
        self.zones = zones
        self.settings = settings
        nz = len(zones.zones) if zones else 0
        self.bins = force_bins(settings)
        self.stable = np.zeros(nz, dtype=int)                    # 구역별 안정 샘플 수
        self.bin_counts = np.zeros((nz, len(self.bins)), dtype=int)  # 구역 × 힘 구간
        self.total_stable = 0
        self.total_contact = 0
        self.no_zone = 0
        self.gauge_samples = 0
        self.last_F_N: Optional[float] = None
        self._lock = threading.Lock()
        self._ft: List[float] = []
        self._ff: List[tuple] = []
        self._fz: List[tuple] = []
        self._gt: List[float] = []
        self._gv: List[float] = []
        self._done_t = -np.inf    # 이 시각까지의 게이지 샘플은 처리함

    # ── sink (리더 스레드) ──
    def add_frame(self, frame) -> None:
        z = tuple(np.nan if v is None else v for v in frame.grid[2::3])
        c = tuple(np.nan if v is None else v for v in frame.combine)
        with self._lock:
            self._ft.append(frame.t)
            self._ff.append(c)
            self._fz.append(z)

    def add_gauge(self, t: float, v: float) -> None:
        with self._lock:
            self._gt.append(t)
            self._gv.append(v)

    # ── 집계 (GUI 타이머) ──
    def enough(self) -> np.ndarray:
        """구역마다 "충분": 안정 샘플 ≥ zone_min_samples 이고 힘 구간 2개 이상 채움."""
        return (self.stable >= int(self.settings["zone_min_samples"])) & ((self.bin_counts > 0).sum(axis=1) >= 2)

    def update(self) -> int:
        """처리할 수 있는 게이지 샘플을 분류해 더한다. 새로 처리한 샘플 수를 돌려준다."""
        s = self.settings
        half, gap = float(s["stable_window_s"]), float(s["max_gap_s"])
        with self._lock:
            if not self._gt or not self._ft:
                return 0
            gt = np.array(self._gt)
            gv = np.array(self._gv, dtype=float)
            ft = np.array(self._ft)
            ff = np.array(self._ff, dtype=float)
            fz = np.array(self._fz, dtype=float)
        ready_until = min(gt[-1] - half, ft[-1] - max(half, gap))
        target = (gt > self._done_t) & (gt <= ready_until)
        if not target.any():
            return 0
        a, b = gt[target][0], gt[target][-1]
        gsel = (gt >= a - half - 1e-9) & (gt <= b + half + 1e-9)
        fsel = (ft >= a - half - gap) & (ft <= b + half + gap)
        series = SensorSeries(ft[fsel], ff[fsel], fz[fsel])
        cols = pair_sensor(gt[gsel], gv[gsel], series, s, self.zones)
        tsel = (cols["t_unix_s"] >= a) & (cols["t_unix_s"] <= b)
        st = cols["stable"][tsel]
        zone = cols["zone"][tsel]
        g = cols["gauge_N"][tsel]
        self.gauge_samples += int(tsel.sum())
        self.total_contact += int(cols["contact"][tsel].sum())
        self.total_stable += int(st.sum())
        bi = np.clip(np.searchsorted(self.bins, g, side="right") - 1, 0, len(self.bins) - 1)
        for k, bk in zip(zone[st], bi[st]):
            if k == NO_ZONE:
                self.no_zone += 1
            elif self.zones is not None:
                self.stable[k] += 1
                self.bin_counts[k, bk] += 1
        self._done_t = b
        if len(ff):
            self.last_F_N = float(np.sqrt(np.nansum((ff[-1] * RAW_TO_N) ** 2)))
        self._trim(b - KEEP_S)
        return int(tsel.sum())

    def _trim(self, before: float) -> None:
        with self._lock:
            i = int(np.searchsorted(np.array(self._ft), before))
            j = int(np.searchsorted(np.array(self._gt), before))
            del self._ft[:i], self._ff[:i], self._fz[:i]
            del self._gt[:j], self._gv[:j]
