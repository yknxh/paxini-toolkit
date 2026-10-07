"""Synthetic gauge test session with known errors (for plan P6-6 verification).

The sensor CSV is written with the same `CsvRecorder` as real recordings (PXSR format). The load L(t) is a trapezoid whose
position (taxel center) and force change with each press (ramp up 0.6 s · hold 1.2 s · ramp down 0.6 s · rest 0.6 s).
Sensor |F| = gain·L + bias (+ taxel_bias[c] when pressing taxel c), no-load is residual_N, sensor timestamps lag by delay_s.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from paxkit.bench.session import GAUGE_FILE, META_FILE
from paxkit.bench.settings import bench_settings
from paxkit.device.geometry import load_geometry
from paxkit.device.frames import Frame
from paxkit.recording import CsvRecorder

START = datetime(2026, 10, 5, 10, 0, 0)
PERIOD = 3.0


@dataclass
class SynthSensor:
    label: str
    model: str = "S1813E"
    channel: int = 0
    slot: int = 0
    gain: float = 1.05
    bias_N: float = 0.2
    residual_N: float = 0.1
    taxel_bias: Dict[int, float] = field(default_factory=dict)   # pressed taxel → extra error N


def load_profile(t: np.ndarray, levels=(4.0, 8.0, 12.0)):
    """(load N, press index). Cycles through levels, one per press."""
    k = np.floor(t / PERIOD).astype(int)
    u = (t / PERIOD) - k
    a = np.clip(np.minimum(u / 0.2, (0.8 - u) / 0.2), 0, 1)
    lv = np.asarray(levels)[k % len(levels)]
    return a * lv, k


class _Src:
    def __init__(self):
        self.sensors: List[List[Optional[Frame]]] = []
        self.infos = {}

    def add_sink(self, *a, **k):
        pass

    def remove_sink(self, *a):
        pass


def make_session(folder: Path, sensors: List[SynthSensor], *, seconds: float = 120.0, delay_s: float = 0.08,
                 sensor_hz: float = 100.0, gauge_hz: float = 10.0, schedule=None, crosstalk: float = 0.0,
                 simultaneous_every: int = 0, events: Optional[List[dict]] = None, settings=None,
                 gauge_outliers: int = 0) -> Path:
    """schedule(k) → index of the sensor pressed at press k (default 0). Press positions step through that sensor's taxels (k*7 % N)."""
    folder.mkdir(parents=True, exist_ok=True)
    t0 = START.timestamp()
    schedule = schedule or (lambda k: 0)
    geoms = [load_geometry(s.model) for s in sensors]

    # gauge
    gt_rel = np.arange(0.0, seconds, 1.0 / gauge_hz) + 0.013
    L, _ = load_profile(gt_rel)
    gv = np.round(L, 1)
    lines = ["t_unix_s,force_N"] + [f"{t0 + t:.6f},{v:.4f}" for t, v in zip(gt_rel, gv)]
    for i in range(gauge_outliers):
        lines.insert(5 + i * 50, f"{t0 + gt_rel[4 + i * 50] + 0.001:.6f},-9000.0000")
    (folder / GAUGE_FILE).write_bytes(("\n".join(lines) + "\n").encode("ascii"))

    # sensor (same clock, stamped delay_s late = sensor values follow the gauge with a delay)
    src = _Src()
    rec = CsvRecorder(src, folder, sidecar=False)
    rec.start(START, attach=False)
    st_rel = np.arange(0.0, seconds, 1.0 / sensor_hz)
    Ls, ks = load_profile(st_rel - delay_s)
    frames_by_ch: Dict[int, List[Optional[Frame]]] = {}
    for t, l, k in zip(st_rel, Ls, ks):
        who = schedule(int(k))
        also = (who + 1) % len(sensors) if simultaneous_every and k % simultaneous_every == simultaneous_every - 1 else None
        for i, s in enumerate(sensors):
            g = geoms[i]
            n = g.n_taxels
            c = (int(k) * 7) % n
            if i == who or i == also:
                load = l
            else:
                load = crosstalk * l
            if load > 0:
                F = s.gain * load + s.bias_N + s.taxel_bias.get(c, 0.0) if (i == who or i == also) else load
                d2 = ((g.taxels - g.taxels[c]) ** 2).sum(axis=1)
                w = np.exp(-d2 / (2 * 2.5 ** 2))
                tz = [int(min(255, round(load * 10 * 0.6 * x))) for x in w] if (i == who or i == also) else [0] * n
            else:
                F = s.residual_N
                tz = [0] * n
            z = int(round(F * 10))
            grid = []
            for v in tz:
                grid += [0, 0, v]
            f = Frame(t=t0 + t, channel=s.channel, slot=s.slot, sensor=s.model, combine=(0, 0, z), grid=tuple(grid))
            row = frames_by_ch.setdefault(s.channel, [])
            if len(row) <= s.slot:
                row.extend([None] * (s.slot + 1 - len(row)))
            row[s.slot] = f
        src.sensors = [frames_by_ch.get(ch, []) for ch in range(max(frames_by_ch) + 1)]
        rec.on_frame(f)
    rec.stop()

    meta = {
        "format": "paxkit-bench/1", "label": sensors[0].label if len(sensors) == 1 else "hand",
        "model": sensors[0].model if len(sensors) == 1 else "HAND", "status": "stopped",
        "sensors": [{"channel": s.channel, "slot": s.slot, "sensor": s.model, "label": s.label} for s in sensors],
        "sensor_csv": rec.path.name, "settings": settings or bench_settings(),
        "events": [dict(e, t_unix_s=t0 + e["t_rel"]) for e in (events or [])],
    }
    (folder / META_FILE).write_bytes(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
    return folder
