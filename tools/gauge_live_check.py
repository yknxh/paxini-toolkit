"""Reads the force gauge directly. When the sensor is read too, estimates the lag on the same clock (plan P5 completion criterion).

    python tools/gauge_live_check.py [COM7] [--seconds 10] [--sensor [COM3]] [--sim]

Gauge only: prints receive rate, value range, latest value and receive intervals.
--sensor: also connects a USB sensor, reads it for the same time, and estimates the lag by cross-correlating gauge N ↔ sensor
          resultant magnitude (raw/10). Needs a few press/release cycles (idle → "cannot estimate"). With no sensor port,
          the CH343 port is auto-selected.
--sim: runs the same flow with a simulated gauge (and sensor), without hardware.

With real hardware, received bytes are saved as-is to `data/gauge_raw/YYYY-MM-DD-HHMMSS.txt` (time + bytes), and for
receive gaps over 0.5 s and out-of-range values (|value| > --max-n) the surrounding bytes are printed (for diagnosis).
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paxkit.config import Config  # noqa: E402
from paxkit.paths import data_path  # noqa: E402
from paxkit.device.sim import SimUsbTransport  # noqa: E402
from paxkit.device.transport import SerialTransport, default_sensor_port, list_serial_ports  # noqa: E402
from paxkit.device.usb import UsbSensor  # noqa: E402
from paxkit.gauge import SerialGauge, SimGauge, sensor_lag  # noqa: E402
from paxkit.gauge.reader import _open_serial  # noqa: E402

GAP_S = 0.5


class RawLog:
    """Wraps a serial object and records the bytes returned by read with their timestamps."""

    def __init__(self, clock):
        self.clock = clock
        self.chunks = []   # (PC time, bytes)

    def open(self, port, baud, timeout):
        ser = _open_serial(port, baud, timeout)
        log = self

        class Wrapped:
            def __enter__(self):
                ser.__enter__()
                return self

            def __exit__(self, *a):
                return ser.__exit__(*a)

            def __getattr__(self, name):
                return getattr(ser, name)

            def read(self, n=1):
                b = ser.read(n)
                if b:
                    log.chunks.append((log.clock.wall(), b))
                return b

        return Wrapped()

    def around(self, t0, t1):
        return b"".join(b for t, b in self.chunks if t0 <= t <= t1)

    def save(self, path):
        lines = [f"{t:.4f} {b.hex()} {b!r}" for t, b in self.chunks]
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--sensor", nargs="?", const="", default=None)
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--max-n", type=float, default=600.0, help="flag |values| above this as out of range (ZP-500N)")
    a = ap.parse_args()
    cfg = Config.load()

    sensor = None
    if a.sensor is not None:
        if a.sim:
            transport = SimUsbTransport("S1813E")
        else:
            port = a.sensor or default_sensor_port()
            if not port:
                print("Sensor port not found. Ports:")
                for p in list_serial_ports():
                    print(f"  {p.device}  {p.description}  {'(CH343)' if p.is_sensor else ''}")
                return 1
            transport = SerialTransport(port)
        sensor = UsbSensor(transport)

    raw = None
    if a.sim:
        gauge = SimGauge(load=sensor.transport.load_N if sensor else None)
    else:
        gauge = SerialGauge({**cfg.section("gauge"), "port": a.port or cfg.get("gauge.port", "COM7")})
        raw = RawLog(gauge.clock)
        gauge.open_serial = raw.open
    samples = []
    gauge.add_sink(lambda t, v: samples.append((t, v)))
    gauge.start()
    if sensor is not None:
        sensor.start()
    print(f"Gauge {gauge.port}{', with sensor' if sensor else ''}: reading for {a.seconds:.0f} s"
          + (" (press and release a few times)" if sensor else ""))
    time.sleep(a.seconds)
    now = gauge.clock.wall()
    lag = sensor_lag(gauge.buffer, sensor.buffer, now, window=a.seconds) if sensor else None
    status = f"{gauge.status} {gauge.error}".strip()   # read first, since it becomes disconnected once stopped
    gauge.stop()
    gauge.join(3)
    if sensor is not None:
        sensor.disconnect()
        sensor.join(5)

    print(f"Gauge status {status}")
    if raw is not None:
        path = data_path("gauge_raw") / (time.strftime("%Y-%m-%d-%H%M%S") + ".txt")
        raw.save(path)
        print(f"Received {sum(len(b) for _, b in raw.chunks)} bytes → {path}")
    if len(samples) < 2:
        print("No gauge values (check port, power, output settings, cable)")
        return 1
    t = np.array([s[0] for s in samples])
    v = np.array([s[1] for s in samples])
    gaps = np.diff(t)
    print(f"Gauge {len(samples)} samples, {len(samples) / (t[-1] - t[0]):.1f} Hz, "
          f"values {v.min():.1f} ~ {v.max():.1f} N, latest {v[-1]:.1f} N")
    print(f"Receive interval ms: median {statistics.median(gaps) * 1e3:.1f}, min {gaps.min() * 1e3:.1f}, "
          f"max {gaps.max() * 1e3:.1f}")
    t_first = t[0]
    for i in np.flatnonzero(gaps > GAP_S):
        print(f"  Receive gap {t[i] - t_first:.2f}s → {t[i + 1] - t_first:.2f}s ({gaps[i]:.2f}s), "
              f"values before/after {v[i]:.1f} → {v[i + 1]:.1f}")
        if raw is not None:
            print(f"    bytes around {raw.around(t[i] - 0.3, t[i + 1] + 0.3)!r}")
    for i in np.flatnonzero(np.abs(v) > a.max_n):
        print(f"  Out-of-range value at {t[i] - t_first:.2f}s: {v[i]:.1f} N")
        if raw is not None:
            print(f"    surrounding bytes {raw.around(t[i] - 0.5, t[i] + 0.3)!r}")
    if sensor is not None:
        print(f"Sensor {sensor.sensor_type.label}, frames {sensor.frame_count}, status {sensor.status} {sensor.error}")
        off, r = lag
        if off is None:
            print("Lag: cannot estimate (gauge variation below 0.3 N or not enough data)")
        else:
            print(f"Lag (sensor - gauge): {off * 1e3:+.0f} ms, correlation {r:.3f}"
                  "  (+ means the sensor is late. The gauge is ~10 Hz, so about ±50 ms is hard to resolve)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
