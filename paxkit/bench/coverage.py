"""Live tally during recording (plan P6-1 3): contact/stable counts and, per test point, the mean |e| %.

Values from the sensor sink (frames) and gauge sink (samples) are buffered; on `update()`, only gauge samples whose surrounding
data needed for pairing has fully arrived are classified with the same function as post-analysis (`pair_sensor`). So the
on-screen counts follow the same rules as the analysis (the analysis also excludes gauge outliers etc., so counts may differ slightly).

Test points (`select_point`): as in the analysis, each sample belongs to the point selected at its time; per point, contact
samples and the mean |e| / gauge % over stable samples ≥ pct_min_N are tallied (without the gauge lag correction the
analysis applies, so values can differ a little).
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Tuple

import numpy as np

from .pairing import RAW_TO_N, SensorSeries, pair_sensor
from .points import assign

KEEP_S = 3.0   # past data kept after processing (for the slope window and interpolation)


class Coverage:
    def __init__(self, settings: Dict) -> None:
        self.settings = settings
        self.total_stable = 0
        self.total_contact = 0
        self.no_point = 0          # stable samples with no test point selected
        self.gauge_samples = 0
        self.last_F_N: Optional[float] = None
        self.points: Dict[str, Dict[str, float]] = {}   # id → n_contact, n_fit, sum_abs_pct
        self._lock = threading.Lock()
        self._ft: List[float] = []
        self._ff: List[tuple] = []
        self._gt: List[float] = []
        self._gv: List[float] = []
        self._sel_t: List[float] = []
        self._sel_ids: List[str] = []
        self._done_t = -np.inf    # gauge samples up to this time have been processed

    # ── test points (GUI thread) ──
    def select_point(self, t: float, pid: Optional[str]) -> None:
        """From time t on, samples belong to point pid (None = no point)."""
        with self._lock:
            self._sel_t.append(float(t))
            self._sel_ids.append(pid or "")
        if pid:
            self.points.setdefault(pid, dict(n_contact=0, n_fit=0, sum_abs_pct=0.0))

    def point_stats(self, pid: str) -> Tuple[int, int, Optional[float]]:
        """(contact samples, samples in the error %, mean |e| / gauge %) of a point."""
        d = self.points.get(pid)
        if d is None:
            return 0, 0, None
        pct = d["sum_abs_pct"] / d["n_fit"] if d["n_fit"] > 0 else None
        return int(d["n_contact"]), int(d["n_fit"]), pct

    # ── sink (reader thread) ──
    def add_frame(self, frame) -> None:
        c = tuple(np.nan if v is None else v for v in frame.combine)
        with self._lock:
            self._ft.append(frame.t)
            self._ff.append(c)

    def add_gauge(self, t: float, v: float) -> None:
        with self._lock:
            self._gt.append(t)
            self._gv.append(v)

    # ── tally (GUI timer) ──
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
            sel_t, sel_ids = np.array(self._sel_t), list(self._sel_ids)
        ready_until = min(gt[-1] - half, ft[-1] - max(half, gap))
        target = (gt > self._done_t) & (gt <= ready_until)
        if not target.any():
            return 0
        a, b = gt[target][0], gt[target][-1]
        gsel = (gt >= a - half - 1e-9) & (gt <= b + half + 1e-9)
        fsel = (ft >= a - half - gap) & (ft <= b + half + gap)
        series = SensorSeries(ft[fsel], ff[fsel], np.zeros((int(fsel.sum()), 0)))
        cols = pair_sensor(gt[gsel], gv[gsel], series, s)
        tsel = (cols["t_unix_s"] >= a) & (cols["t_unix_s"] <= b)
        st, ct = cols["stable"][tsel], cols["contact"][tsel]
        g, e = cols["gauge_N"][tsel], cols["error_N"][tsel]
        pid = assign(cols["t_unix_s"][tsel], sel_t, sel_ids)
        lo = float(s.get("pct_min_N", 0.0))
        for q, c, sk, ek, gk in zip(pid, ct, st, e, g):
            if not q:
                self.no_point += int(sk)
            elif c and np.isfinite(ek):
                d = self.points[q]
                d["n_contact"] += 1
                if sk and gk >= lo and gk > 0:
                    d["n_fit"] += 1
                    d["sum_abs_pct"] += 100.0 * abs(float(ek)) / float(gk)
        self.gauge_samples += int(tsel.sum())
        self.total_contact += int(ct.sum())
        self.total_stable += int(st.sum())
        self._done_t = b
        if len(ff):
            self.last_F_N = float(np.sqrt(np.nansum((ff[-1] * RAW_TO_N) ** 2)))
        self._trim(b - KEEP_S)
        return int(tsel.sum())

    def _trim(self, before: float) -> None:
        with self._lock:
            i = int(np.searchsorted(np.array(self._ft), before))
            j = int(np.searchsorted(np.array(self._gt), before))
            del self._ft[:i], self._ff[:i]
            del self._gt[:j], self._gv[:j]
