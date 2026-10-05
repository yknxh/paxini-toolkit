"""Data logging — port of the PXSR logging start/stop buttons and logging classes (`gs0`, `cs0`).

| PXSR (offset)                            | Here |
|------------------------------------------|------|
| `K0` start logging (in Hh0, ~454200)     | `CsvRecorder.start()` |
| `d2` add row (called by `y0` per 1008)   | `CsvRecorder.on_frame()` |
| `G1` stop logging                        | `CsvRecorder.stop()` |
| `gs0` (343437)                           | file name = start time, header (`W0`) fixed at first row |
| `cs0` (342548)                           | writer created at first row + flush every 200 ms, remaining rows flushed on stop |

File byte rules are in `pxsr_csv.py`. Order of operations:
- Start: only the file name is decided. The file is not created yet.
- First row: header fixed, 200 ms flush loop starts. The file appears at the first flush (or at stop, if stopped before).
- Stopping with no rows creates no file.
- Two starts within the same second share a file name, so the later log's first write (`'w'`) overwrites the earlier file (same as PXSR).

Differences from PXSR (no effect on file bytes):
- PXSR can start the next flush without waiting for the previous one, so if the second write starts before the first ends
  the header can appear twice (one write takes a few ms so it does not happen in practice, 0 of 39 PXSR CSVs).
  Here writes run one after another.
- Flushing runs on a separate thread so the receive thread is not blocked by disk writes.

The `.json` sidecar is an extra file not in PXSR (user decision, CLAUDE.md). It does not affect CSV bytes.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..paths import data_path
from . import pxsr_csv

log = logging.getLogger(__name__)

FLUSH_INTERVAL_S = 0.2   # gs0: `new cs0(filename).writeHeader(o).setInterval(200)`
SIDECAR_FORMAT = "paxkit-log-sidecar/1"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


class CsvRecorder:
    """Data logging for one sensor (`UsbSensor`). A new object is created for every start (PXSR also does `new gs0`).

    source is a reader with `sensors` (PXSR `v.value`) and `add_sink(fn, on_stop)` / `remove_sink(fn)`.
    `on_frame` is called on the reader thread, and each row is built from the whole `source.sensors` at that moment (PXSR `y0`).
    """

    def __init__(self, source, directory: Optional[Path] = None, *, interval: float = FLUSH_INTERVAL_S,
                 sidecar: bool = True, info: Optional[Dict[str, Any]] = None) -> None:
        self.source = source
        self.directory = Path(directory) if directory is not None else None
        self.interval = interval
        self.sidecar = sidecar
        self.info: Dict[str, Any] = dict(info or {})   # extra info for the sidecar (connection mode, port, etc.)
        self.memo = ""

        self.path: Optional[Path] = None
        self.started_at: Optional[float] = None
        self.stopped_at: Optional[float] = None
        self.line_count = 0             # PXSR `e.value.lineCount`
        self.header: Optional[List[str]] = None
        self.events: List[Dict[str, Any]] = []
        self.error = ""

        self._active = False            # PXSR `o.value`
        self._lock = threading.Lock()   # state and row cache
        self._io_lock = threading.Lock()   # serializes flushes
        self._cache: List[list] = []    # cs0 `dataCache`
        self._writer: Optional[pxsr_csv.CsvFileWriter] = None
        self._layout: List[Dict[str, Any]] = []
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._attached = False

    # ── PXSR buttons ─────────────────────────────────────────────────
    def start(self, now: Optional[datetime] = None, *, attach: bool = True) -> Path:
        """`K0`: `new gs0(...)` → file name = current time. With attach=False no sink is registered; call `on_frame` directly."""
        start = now or datetime.now()
        directory = self.directory or data_path("logs")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / pxsr_csv.log_filename(start)
        self.started_at = start.timestamp()
        self._active = True
        if attach:
            self.source.add_sink(self.on_frame, on_stop=self.stop)
            self._attached = True
            if hasattr(self.source, "add_listener"):
                self.source.add_listener(self._on_source_event)
        return self.path

    def _on_source_event(self, kind: str, info: Dict[str, Any]) -> None:
        """Record the reader's receive stall in sidecar events (the reader stops logging after the event)."""
        if kind == "stalled":
            last = info.get("t")
            self.note_event("stalled", None, last_frame=_iso(last) if last else None, stall_s=info.get("stall_s"),
                            message="USB receive stalled → saved up to the last frame and stopped logging")

    def on_frame(self, frame) -> None:
        """`d2(T1)` → `gs0.writeData` → `cs0.writeData`."""
        with self._lock:
            if not self._active:
                return
            sensors = self.source.sensors
            row = pxsr_csv.build_row(pxsr_csv.format_timestamp(frame.t), sensors)
            if self._writer is None:
                # gs0: on first writeData, header via createScvHeaderCallback (=W0) → create cs0
                # cs0: on first writeData, createArrayCsvWriter + startFlushLoop
                self.header = pxsr_csv.build_header(sensors)
                self._writer = pxsr_csv.CsvFileWriter(self.path, self.header)
                self._layout = self._sensor_layout(sensors)
                self._thread = threading.Thread(target=self._flush_loop, daemon=True, name="CsvRecorder")
                self._thread.start()
            self._cache.append(row)
            self.line_count += 1

    def stop(self) -> None:
        """`G1`: `o.value=false` → `gs0.stop()` (flush remaining rows). Safe to call more than once."""
        with self._lock:
            if not self._active:
                return
            self._active = False
            self.stopped_at = time.time()
        if self._attached:
            self.source.remove_sink(self.on_frame)
            if hasattr(self.source, "remove_listener"):
                self.source.remove_listener(self._on_source_event)
            self._attached = False
        self._stop_evt.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        self._flush()
        self._write_sidecar()

    # ── State ────────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        return self._active

    @property
    def file_exists(self) -> bool:
        return self._writer is not None and self._writer.append

    def note_event(self, kind: str, t: Optional[float] = None, **info: Any) -> None:
        """Record things that happen during logging, such as calibration, in the sidecar (not in the CSV)."""
        with self._lock:
            if self._active:
                self.events.append({"time": _iso(t if t is not None else time.time()), "kind": kind, **info})

    # ── Internal ─────────────────────────────────────────────────────
    def _flush_loop(self) -> None:
        """cs0 `startFlushLoop`: `setInterval(() => flushData(false), 200)`."""
        while not self._stop_evt.wait(self.interval):
            first = not self.file_exists
            self._flush()
            if first and self.file_exists:
                self._write_sidecar()

    def _flush(self) -> None:
        """cs0 `flushData`: does nothing if the cache is empty."""
        with self._io_lock:
            with self._lock:
                rows, self._cache = self._cache, []
            if not rows or self._writer is None:
                return
            try:
                self._writer.write_records(rows)
            except OSError as e:
                log.exception("CSV write failed")
                self.error = str(e)

    def _sensor_layout(self, sensors) -> List[Dict[str, Any]]:
        src = self.source
        out = []
        for ch, slots in enumerate(sensors):
            for slot, f in enumerate(slots or ()):
                if f is None:
                    continue
                d = {"channel": ch, "slot": slot, "sensor": f.sensor, "taxels": len(f.grid) // 3}
                infos = getattr(src, "infos", {}) or {}
                if infos.get(slot):
                    d["version"] = infos[slot]
                out.append(d)
        return out

    def _write_sidecar(self) -> None:
        if not self.sidecar or not self.file_exists:
            return
        with self._lock:
            doc = {
                "format": SIDECAR_FORMAT,
                "csv": self.path.name,
                "started": _iso(self.started_at),
                "stopped": _iso(self.stopped_at) if self.stopped_at else None,
                "rows": self.line_count,
                **self.info,
                "sensors": self._layout,
                "events": list(self.events),
                "memo": self.memo,
            }
        try:
            self.path.with_suffix(".json").write_bytes(
                json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
        except OSError:
            log.exception("sidecar write failed")
