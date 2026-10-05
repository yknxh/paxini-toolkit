"""알려진 오차를 넣은 합성 게이지 테스트 세션 (계획 P6-6 검증용).

센서 CSV는 실제 기록과 같은 `CsvRecorder`로 쓴다 (PXSR 형식). 하중 L(t)는 누름마다 위치(taxel 중심)와 힘이 바뀌는
사다리꼴 (올림 0.6 s · 유지 1.2 s · 내림 0.6 s · 쉼 0.6 s).
센서 |F| = gain·L + bias (+ zone_bias_N, 그 구역을 누를 때), 무부하는 residual_N, 센서 시각은 delay_s 만큼 늦음.
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
from paxkit.bench.zones import load_zones
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
    zone_bias: Dict[str, float] = field(default_factory=dict)   # 구역 id → 추가 오차 N


def load_profile(t: np.ndarray, levels=(4.0, 8.0, 12.0)):
    """(하중 N, 누름 번호). 누름마다 levels를 돌아가며."""
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
    """schedule(k) → 누름 k에서 눌리는 센서 번호 (기본 0). 누름 위치는 그 센서 taxel을 차례로 (k*7 % N)."""
    folder.mkdir(parents=True, exist_ok=True)
    t0 = START.timestamp()
    schedule = schedule or (lambda k: 0)
    zsets = [load_zones(s.model) for s in sensors]

    # 게이지
    gt_rel = np.arange(0.0, seconds, 1.0 / gauge_hz) + 0.013
    L, _ = load_profile(gt_rel)
    gv = np.round(L, 1)
    lines = ["t_unix_s,force_N"] + [f"{t0 + t:.6f},{v:.4f}" for t, v in zip(gt_rel, gv)]
    for i in range(gauge_outliers):
        lines.insert(5 + i * 50, f"{t0 + gt_rel[4 + i * 50] + 0.001:.6f},-9000.0000")
    (folder / GAUGE_FILE).write_bytes(("\n".join(lines) + "\n").encode("ascii"))

    # 센서 (같은 시계, delay_s 늦게 찍힘 = 센서 값이 게이지보다 늦게 따라감)
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
            g = zsets[i].geometry
            n = g.n_taxels
            c = (int(k) * 7) % n
            if i == who or i == also:
                load = l
            else:
                load = crosstalk * l
            if load > 0:
                zid = zsets[i].zones[zsets[i].taxel_zone()[c]].id
                F = s.gain * load + s.bias_N + s.zone_bias.get(zid, 0.0) if (i == who or i == also) else load
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
