"""Read a USB direct sensor without PXSR (PXSR must be closed to open the port).

    python tools/usb_live_check.py [COM3] [--seconds 10] [--calibrate] [--record]

Connect → receive for the given time → disconnect. Prints version, sensor type, receive rate, request interval and latest values.
--calibrate: send a calibration command midway (changes the sensor zero, so only with nothing pressed).
             Prints the result (response, before/after resultant) and appends it to `data/calibration/history.jsonl`.
--record: log a PXSR-format CSV to `data/logs/` while receiving, and check that row count = frame count.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paxkit.calibration import OUTCOME_TEXT, CalibrationRun, append_history  # noqa: E402
from paxkit.device.transport import SerialTransport, default_sensor_port, list_serial_ports  # noqa: E402
from paxkit.device.usb import UsbSensor  # noqa: E402
from paxkit.recording import CsvRecorder  # noqa: E402


class LoggingTransport(SerialTransport):
    """Record the time of each sent command (for checking request intervals)."""

    def __init__(self, port):
        super().__init__(port)
        self.tx = []

    def write(self, data):
        self.tx.append((time.perf_counter(), bytes(data)))
        super().write(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    port = a.port or default_sensor_port()
    if not port:
        print("Sensor port not found. Ports:")
        for p in list_serial_ports():
            print(f"  {p.device}  {p.description}  {'(CH343)' if p.is_sensor else ''}")
        return 1
    tr = LoggingTransport(port)
    events = []
    s = UsbSensor(tr, on_event=lambda k, info: events.append((time.perf_counter(), k, info)))
    frames = []
    s.add_sink(frames.append)
    t0 = time.perf_counter()
    s.start()
    rec = None
    if a.record:
        rec = CsvRecorder(s, info={"mode": "usb", "port": port, "tool": "usb_live_check"})
        rec.start()   # fine to start before the first frame (the file appears after the first row)
    cal = None
    if a.calibrate:
        time.sleep(a.seconds / 2)
        cal = CalibrationRun(s, {"mode": "usb", "port": port, "tool": "usb_live_check"}).start()
        t_cal = time.monotonic()
        cal.wait(timeout=a.seconds / 2)
        time.sleep(max(0.0, a.seconds / 2 - (time.monotonic() - t_cal)))
        if cal.result.done:
            append_history(cal.result)
            if rec is not None:
                d = cal.result.to_dict()
                d.pop("requested")
                rec.note_event("calibration", cal.result.requested, **d)
    else:
        time.sleep(a.seconds)
    s.disconnect()
    s.join(5)

    for t, k, info in events:
        print(f"  [{t - t0:7.3f}s] {k} {info}")
    print(f"port {port}, status {s.status} {s.error}")
    print(f"sensor {s.sensor_type.label} (taxel {s.sensor_type.forces}), serviceID {s.service_id}, slot {s.slot}")
    data = [(t, d) for t, d in tr.tx if d[6] == 0xFB and d[7:9] == bytes([0xF0, 0x03])]
    if len(frames) > 1:
        dur = frames[-1].t - frames[0].t
        print(f"{len(frames)} frames, {len(frames) / dur:.1f} Hz, header errors {s.header_errors}")
        gaps = [b[0] - a_[0] for a_, b in zip(data, data[1:])]
        q = statistics.quantiles(gaps, n=100)
        print(f"data request interval ms: median {statistics.median(gaps) * 1e3:.2f}, "
              f"1% {q[0] * 1e3:.2f}, 99% {q[98] * 1e3:.2f}, max {max(gaps) * 1e3:.2f}")
        f = frames[-1]
        print(f"last resultant raw {f.combine} → N {[v / 10 for v in f.combine]}")
    if cal is not None:
        r = cal.result
        ms = lambda x, y: "-" if x is None or y is None else f"{(y - x) * 1e3:.1f} ms"
        print(f"calibration: {OUTCOME_TEXT.get(r.outcome, r.outcome)}, status {r.status}, function code {r.function_code}")
        print(f"  button→send {ms(r.requested, r.sent)}, send→response {ms(r.sent, r.acked)}")
        print(f"  resultant raw mean before {r.before} → after {r.after}")
    if rec is not None:
        n_rows = rec.path.read_bytes().count(b"\n") - 1 if rec.path.exists() else 0
        print(f"log {rec.path} : rows {n_rows}, recorder {rec.line_count}, frames {len(frames)} "
              f"({'match' if n_rows == rec.line_count == len(frames) else 'MISMATCH'})")
    print("first commands sent:")
    for t, d in tr.tx[:11]:
        print(f"  {t - t0:7.3f}s {d.hex()}")
    return 0 if s.status != "error" and frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
