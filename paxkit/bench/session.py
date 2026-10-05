"""Gauge test session recording (plan P6-1, 4.2): `data/bench/YYYY-MM-DD-HHMMSS_<model>_<label>/`.

- Sensor: the same `CsvRecorder` as regular logging (directory = session folder) → PXSR-identical CSV + `.json` sidecar.
- Gauge: `gauge.csv` (`t_unix_s,force_N`, paxtest format). Timestamps use the same `clock.wall()` as the sensor.
- `meta.json`: format version, connection/sensor/gauge info, all test settings, start/stop times, events (no-load check, calibration, etc.).
The analysis reads only these files (`analyze.py`). No correction is applied to sensor values.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .. import __version__
from ..paths import data_path
from ..recording import CsvRecorder

META_FORMAT = "paxkit-bench/1"
GAUGE_FILE = "gauge.csv"
META_FILE = "meta.json"


def _iso(t: Optional[float]) -> Optional[str]:
    return None if t is None else datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


def safe_label(label: str) -> str:
    """Make it usable as a folder name: spaces/path characters → `_`. `noname` if empty."""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", label.strip()).strip("._")
    return s or "noname"


def noload_check(sensor_buffer, gauge_buffer, t0: float, t1: float, warn_N: float) -> Dict[str, Any]:
    """No-load check (hand off): gauge and sensor means over [t0, t1]. Recorded only, never used to correct values."""
    ts, vs = sensor_buffer.snapshot(since=t0)
    vs = vs[ts <= t1] * 0.1
    tg, vg = gauge_buffer.snapshot(since=t0)
    vg = vg[tg <= t1, 0]
    out: Dict[str, Any] = {"t0": _iso(t0), "seconds": round(t1 - t0, 3),
                           "n_sensor": int(len(vs)), "n_gauge": int(len(vg))}
    out["gauge_mean_N"] = round(float(vg.mean()), 4) if len(vg) else None
    if len(vs):
        xyz = vs.mean(axis=0)
        out["sensor_mean_N"] = [round(float(v), 4) for v in xyz]
        out["sensor_F_mean_N"] = round(float(np.linalg.norm(vs, axis=1).mean()), 4)
    else:
        out["sensor_mean_N"] = out["sensor_F_mean_N"] = None
    warn = []
    if out["gauge_mean_N"] is None:
        warn.append("No gauge values")
    elif abs(out["gauge_mean_N"]) > warn_N:
        warn.append(f"Gauge no-load {out['gauge_mean_N']:+.2f} N (check the gauge zero button)")
    if out["sensor_F_mean_N"] is None:
        warn.append("No sensor values")
    elif out["sensor_F_mean_N"] > warn_N:
        warn.append(f"Sensor no-load |F| {out['sensor_F_mean_N']:.2f} N (calibration recommended)")
    out["warnings"] = warn
    return out


class BenchSession:
    """One session. Events from the preparation stage (no-load check, etc.) are collected with `note_event` and written with the folder on `start()`."""

    def __init__(self, sensor, gauge, settings: Dict[str, Any], *, label: str, model: str,
                 sensor_info: Optional[Dict[str, Any]] = None, gauge_info: Optional[Dict[str, Any]] = None,
                 gauge_config: Optional[Dict[str, Any]] = None, root: Optional[Path] = None) -> None:
        self.sensor = sensor
        self.gauge = gauge
        self.settings = dict(settings)
        self.label = label
        self.model = model
        self.sensor_info = dict(sensor_info or {})
        self.gauge_info = dict(gauge_info or {})
        self.gauge_config = dict(gauge_config or {})
        self.root = root
        self.events: List[Dict[str, Any]] = []
        self.folder: Optional[Path] = None
        self.recorder: Optional[CsvRecorder] = None
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self.status = "ready"     # ready | recording | stopped | cancelled
        self.gauge_rows = 0
        self.sensors: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._gauge_file = None

    # ── events ──
    def note_event(self, kind: str, t: Optional[float] = None, **info: Any) -> None:
        t = time.time() if t is None else t
        with self._lock:
            self.events.append({"time": _iso(t), "t_unix_s": round(t, 6), "kind": kind, **info})
        if self.recorder is not None and kind == "calibration":
            self.recorder.note_event(kind, t, **info)   # also into the sensor recording sidecar (same as regular logging)
        if self.status == "recording":
            self._write_meta()

    # ── recording ──
    def start(self, now: Optional[datetime] = None) -> Path:
        now = now or datetime.now()
        root = self.root or data_path("bench")
        self.folder = root / f"{now:%Y-%m-%d-%H%M%S}_{safe_label(self.model)}_{safe_label(self.label)}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.started_at = now.timestamp()
        self.sensors = self._sensor_list()
        self._gauge_file = open(self.folder / GAUGE_FILE, "wb")
        self._gauge_file.write(b"t_unix_s,force_N\n")
        self.status = "recording"
        self.recorder = CsvRecorder(self.sensor, self.folder, info=self.sensor_info)
        self.recorder.start(now)
        self.gauge.add_sink(self._on_gauge)
        self._write_meta()
        return self.folder

    def _on_gauge(self, t: float, v: float) -> None:
        with self._lock:
            if self._gauge_file is None:
                return
            self._gauge_file.write(f"{t:.6f},{v:.4f}\n".encode("ascii"))
            self.gauge_rows += 1

    def stop(self, cancelled: bool = False) -> None:
        if self.status != "recording":
            return
        self.gauge.remove_sink(self._on_gauge)
        if self.recorder is not None:
            self.recorder.stop()
        with self._lock:
            f, self._gauge_file = self._gauge_file, None
        if f is not None:
            f.close()
        self.stopped_at = time.time()
        self.status = "cancelled" if cancelled else "stopped"
        self._write_meta()

    @property
    def sensor_csv(self) -> Optional[Path]:
        r = self.recorder
        return r.path if r is not None and r.file_exists else None

    @property
    def sensor_rows(self) -> int:
        return self.recorder.line_count if self.recorder is not None else 0

    # ── internal ──
    def _sensor_list(self) -> List[Dict[str, Any]]:
        s = self.sensor
        st = getattr(s, "sensor_type", None)
        return [{"channel": 0, "slot": int(getattr(s, "slot", 0)), "sensor": getattr(st, "label", self.model),
                 "taxels": int(getattr(st, "forces", 0)), "label": self.label,
                 "version": getattr(s, "version", "") or ""}]

    def meta(self) -> Dict[str, Any]:
        with self._lock:
            events = list(self.events)
        return {
            "format": META_FORMAT,
            "paxkit": __version__,
            "label": self.label,
            "model": self.model,
            "status": self.status,
            "started": _iso(self.started_at),
            "stopped": _iso(self.stopped_at),
            "started_unix_s": self.started_at,
            "stopped_unix_s": self.stopped_at,
            "connection": self.sensor_info,
            "sensors": self.sensors,
            "sensor_csv": self.recorder.path.name if self.recorder and self.recorder.path else None,
            "sensor_rows": self.sensor_rows,
            "gauge": {**self.gauge_info, "config": self.gauge_config, "file": GAUGE_FILE, "rows": self.gauge_rows},
            "settings": self.settings,
            "events": events,
        }

    def _write_meta(self) -> None:
        if self.folder is None:
            return
        (self.folder / META_FILE).write_bytes(
            json.dumps(self.meta(), ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
