"""Coverage tally during recording (plan P6-1 3): stable samples per zone and force-bin fill.

Values from the sensor sink (frames) and gauge sink (samples) are buffered; on `update()`, only gauge samples whose surrounding
data needed for pairing has fully arrived are classified with the same function as post-analysis (`pair_sensor`). So the
on-screen counts follow the same rules as the analysis (the analysis also excludes gauge outliers etc., so counts may differ slightly).
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

import numpy as np

from .pairing import RAW_TO_N, SensorSeries, pair_sensor
from .settings import force_bins
from .zones import NO_ZONE, ZoneSet

KEEP_S = 3.0   # past data kept after processing (for the slope window and interpolation)


class Coverage:
    def __init__(self, zones: Optional[ZoneSet], settings: Dict) -> None:
        self.zones = zones
        self.settings = settings
        nz = len(zones.zones) if zones else 0
        self.bins = force_bins(settings)
        self.stable = np.zeros(nz, dtype=int)                    # stable samples per zone
        self.bin_counts = np.zeros((nz, len(self.bins)), dtype=int)  # zone × force bin
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
        self._done_t = -np.inf    # gauge samples up to this time have been processed

    # ── sink (reader thread) ──
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

    # ── tally (GUI timer) ──
    def enough(self) -> np.ndarray:
        """Per zone "enough": stable samples ≥ zone_min_samples and at least 2 force bins filled."""
        return (self.stable >= int(self.settings["zone_min_samples"])) & ((self.bin_counts > 0).sum(axis=1) >= 2)

    def update(self) -> int:
        """Classifies and adds the gauge samples that can be processed. Returns the number of newly processed samples."""
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
