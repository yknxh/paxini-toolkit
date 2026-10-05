"""One calibration run and result tracking.

Commands, order and waits sent to the sensor are handled by the reader (`UsbSensor.calibrate`, PXSR `O3` 454624),
because it must run inside the reader's event loop to keep the same execution order as PXSR.
This module only records what happened after the button press (no commands to the sensor, no app-side correction).

Outcome follows what the PXSR screen shows:
- When the response (addr 3) arrives, PXSR resumes polling without checking the status and shows no message → "ack".
- If the response has function code 126 and a non-zero status byte, PXSR shows a `Setting.failed` warning → "failed"
  (polling still resumes).
- If no response arrives, PXSR leaves polling stopped (no retry) → "no_ack". Receiving resumes only after reconnecting.
  The response wait (`ack_timeout`) is not a PXSR value (for display only).

Before/after values (`before`, `after`) only show resultant raw means (not applied to measurement data).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..paths import data_path

ACK_TIMEOUT_S = 2.0   # shown as "no_ack" if no response within this time after sending setCalibration
WINDOW_S = 0.5        # averaging window for before/after values

OUTCOME_TEXT = {
    "pending": "in progress",
    "ack": "response received (PXSR: no message, polling resumed)",
    "failed": "failure response (PXSR: Setting.failed warning, polling resumed)",
    "no_ack": "no response (receiving stopped as in PXSR, reconnect needed)",
    "disconnected": "disconnected before response",
}


def _iso(t: Optional[float]) -> Optional[str]:
    return None if t is None else datetime.fromtimestamp(t).isoformat(timespec="milliseconds")


@dataclass
class CalibrationResult:
    requested: float                     # PC time of the button press (Unix s)
    sent: Optional[float] = None         # time setCalibration was sent
    acked: Optional[float] = None        # time the response was handled
    status: Optional[int] = None         # parseUsbData status
    function_code: Optional[int] = None  # response function code (121 in captures)
    failed: bool = False                 # response for which PXSR shows a Setting.failed warning
    outcome: str = "pending"             # pending | ack | failed | no_ack | disconnected
    before: Optional[List[float]] = None  # resultant raw mean over WINDOW_S before the request [X, Y, Z]
    after: Optional[List[float]] = None   # resultant raw mean over WINDOW_S after the response
    info: Dict[str, Any] = field(default_factory=dict)   # port, sensor type, serviceID, version

    @property
    def done(self) -> bool:
        return self.outcome != "pending"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for k in ("requested", "sent", "acked"):
            d[k] = _iso(d[k])
        return d


def _mean(buffer, t0: float, t1: float) -> Optional[List[float]]:
    t, v = buffer.snapshot(since=t0)
    v = v[t <= t1]
    if len(v) == 0:
        return None
    return [round(float(x), 2) for x in np.mean(v, axis=0)]


class CalibrationRun:
    """One button press. After `start()`, call `poll()` periodically or `wait()` until done.

    Double clicks are not blocked (as in PXSR), so if pressed again while running, each run picks up the first send/response after its own start.
    """

    def __init__(self, sensor, info: Optional[Dict[str, Any]] = None, *,
                 ack_timeout: float = ACK_TIMEOUT_S, window: float = WINDOW_S) -> None:
        self.sensor = sensor
        self.ack_timeout = ack_timeout
        self.window = window
        self._lock = threading.Lock()
        self.result = CalibrationResult(requested=0.0, info={
            "sensor": sensor.sensor_type.label, "service_id": sensor.service_id,
            "slot": sensor.slot, "version": sensor.version, **(info or {})})

    def start(self) -> "CalibrationRun":
        r = self.result
        r.requested = self.sensor.clock.wall()
        r.before = _mean(self.sensor.buffer, r.requested - self.window, r.requested)
        self.sensor.add_listener(self._on_event)
        self.sensor.calibrate()
        return self

    def _on_event(self, kind: str, info: dict) -> None:
        with self._lock:
            r = self.result
            if r.done:
                return
            if kind == "calibration_sent" and r.sent is None:
                r.sent = info.get("t")
            elif kind == "calibration_ack" and r.sent is not None and r.acked is None:
                r.acked = info.get("t")
                r.status = info.get("status")
                r.function_code = info.get("function_code")
                r.failed = bool(info.get("failed"))
            elif kind in ("disconnected", "error") and r.acked is None:
                self._finish("disconnected")

    def _finish(self, outcome: str) -> None:
        self.result.outcome = outcome
        self.sensor.remove_listener(self._on_event)

    def poll(self, now: Optional[float] = None) -> bool:
        """True when done. now is PC time (default: sensor clock)."""
        now = self.sensor.clock.wall() if now is None else now
        with self._lock:
            r = self.result
            if r.done:
                return True
            if r.acked is not None:
                if now >= r.acked + self.window:
                    r.after = _mean(self.sensor.buffer, r.acked, r.acked + self.window)
                    self._finish("failed" if r.failed else "ack")
            elif r.sent is not None and now >= r.sent + self.ack_timeout:
                self._finish("no_ack")
            return r.done

    def wait(self, timeout: float = 10.0, interval: float = 0.02) -> CalibrationResult:
        end = time.monotonic() + timeout
        while not self.poll() and time.monotonic() < end:
            time.sleep(interval)
        return self.result


def history_path() -> Path:
    return data_path("calibration") / "history.jsonl"


def append_history(result: CalibrationResult, path: Optional[Path] = None) -> Path:
    """Append one line per calibration run (`data/calibration/history.jsonl`). No values are stored."""
    path = path or history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write((json.dumps(result.to_dict(), ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def read_history(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = path or history_path()
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
