"""Force gauge reader (ported from paxtest `devices/gauge.py`, plan P5).

The real device (SerialGauge) follows the gauge section of config.yaml.
Generalized to poll/stream modes and regex parsing.
Measured (COM7, FTDI): 2400 baud 8N1, sends fixed-width 6-character records ("0000.2", "-004.9") continuously at ~10 Hz without a command or delimiter.
Commands (D, etc.) are ignored and do not change the send rate.
→ with mode: stream, line_terminator: "", records are cut out with record_regex.

Differences from paxtest (the gauge is not a PXSR replacement target, so the byte-identity rule does not apply):
- Receive times are stamped with the same clock as the sensor reader (`clock.wall()`). Windows `time.time()` has 15.6 ms
  resolution, so mixing it with the sensor (precise time) misaligns timestamps.
- Lag correction (2026-10-05 user request "fix the gauge lag as much as possible"). The measured lag (sensor−gauge) of −50 ms is subtracted in two parts:
  1. stream record time = arrival time of the record's **first byte** (paxtest: last byte). The value is fixed before sending
     starts, so it is moved earlier by the record transmission time (6 bytes × 10/2400 s = 25 ms), computed from baud and length.
  2. The remaining fixed latency `latency_s` (config; gauge internal measure→send + FTDI buffer latency timer). Measured lag minus 1.
- The serial-port open function (`open_serial`) is replaceable (testing without hardware).
- SimGauge takes a load function (`load`) instead of paxtest SimWorld. Giving it the same load as the simulated sensor lets them be overlaid.
"""
from __future__ import annotations

import logging
import math
import re
import threading
from typing import Callable, List, Optional

import numpy as np

from ..buffers import TimeSeriesBuffer
from ..device.clock import RealClock

log = logging.getLogger(__name__)
Sink = Callable[[float, float], None]


def _open_serial(port: str, baudrate: int, timeout: float):
    import serial  # pyserial
    return serial.Serial(port, baudrate, timeout=timeout, write_timeout=timeout)


class GaugeBase(threading.Thread):
    name_label = "gauge"

    def __init__(self, clock=None) -> None:
        super().__init__(daemon=True)
        self.clock = clock or RealClock()
        self.buffer = TimeSeriesBuffer(maxlen=120_000, ncols=1)
        self.status = "disconnected"   # disconnected | connected | error
        self.error = ""
        self._sinks: List[Sink] = []
        self._sink_lock = threading.Lock()
        self._stop_evt = threading.Event()

    # ── sinks (the session recorder attaches here) ──
    def add_sink(self, fn: Sink) -> None:
        with self._sink_lock:
            self._sinks.append(fn)

    def remove_sink(self, fn: Sink) -> None:
        with self._sink_lock:
            if fn in self._sinks:
                self._sinks.remove(fn)

    def _emit(self, t: float, value: float) -> None:
        self.buffer.append(t, value)
        with self._sink_lock:
            for fn in self._sinks:
                try:
                    fn(t, value)
                except Exception:  # so a recording failure doesn't kill the read loop
                    log.exception("gauge sink failed")

    def latest(self) -> Optional[float]:
        _, v = self.buffer.latest()
        return None if v is None else float(v[0])

    def receiving(self, window: float = 2.0) -> bool:
        """Connected and a value arrived within the last window seconds (paxtest `hub.gauge_receiving`)."""
        t, _ = self.buffer.latest()
        return self.status == "connected" and t is not None and self.clock.wall() - t < window

    def stop(self) -> None:
        self._stop_evt.set()


class SerialGauge(GaugeBase):
    def __init__(self, cfg: dict, *, clock=None, open_serial=None) -> None:
        super().__init__(clock)
        self.cfg = cfg
        self.open_serial = open_serial or _open_serial
        self.regex = re.compile(cfg.get("line_regex", r"([-+]?\d+(?:\.\d+)?)"))
        self.scale = float(cfg.get("unit_scale", 1.0)) * (-1.0 if cfg.get("invert") else 1.0)
        # for streams where values arrive concatenated without a delimiter (e.g. "0000.0-000.3-004.9...")
        self.record_regex = re.compile(cfg.get("record_regex", r"[-+ \d]\d{3}\.\d").encode())
        self.record_len = int(cfg.get("record_len", 6))
        self.latency = float(cfg.get("latency_s", 0.0))   # fixed latency subtracted from the receive time (s)

    @property
    def port(self) -> str:
        return str(self.cfg.get("port", "COM3"))

    def _split_records(self, buf: bytes) -> tuple[list[tuple[int, float]], bytes]:
        """Extracts completed records from buf as (end position, value) and returns the remaining tail.

        A record at the end of the buffer counts as complete only once it reaches record_len ("-00.7" may be the start of "-00.73").
        """
        out: list[tuple[int, float]] = []
        rest = 0
        for m in self.record_regex.finditer(buf):
            if m.end() == len(buf) and m.end() - m.start() < self.record_len:
                rest = m.start()
                break
            value = self._parse(m.group(0))
            if value is not None:
                out.append((m.end(), value))
            rest = m.end()
        else:
            rest = max(rest, len(buf) - self.record_len)   # drop unmatched noise but keep a partially received record
        return out, buf[rest:]

    def _parse(self, raw: bytes) -> Optional[float]:
        text = raw.decode("ascii", errors="ignore").strip()
        if not text:
            return None
        m = self.regex.search(text)
        if not m:
            return None
        try:
            return float(m.group(1) if m.groups() else m.group(0)) * self.scale
        except ValueError:
            return None

    def run(self) -> None:
        cfg = self.cfg
        term = cfg.get("line_terminator", "\r").encode()
        mode = cfg.get("mode", "poll")
        cmd = str(cfg.get("poll_command", "D\r")).encode()
        period = 1.0 / float(cfg.get("poll_hz", 50))
        baud = int(cfg.get("baudrate", 19200))
        byte_s = 10.0 / baud   # transmission time of one byte at 8N1
        timeout = float(cfg.get("timeout_s", 0.2))
        wall = self.clock.wall
        rec_s = self.record_len * byte_s   # transmission time of one record
        lat = self.latency
        while not self._stop_evt.is_set():
            try:
                with self.open_serial(self.port, baud, timeout) as ser:
                    self.status, self.error = "connected", ""
                    log.info("gauge connected on %s", self.port)
                    ser.reset_input_buffer()
                    buf = b""
                    while not self._stop_evt.is_set() and mode == "stream" and not term:
                        chunk = ser.read(ser.in_waiting or 1)
                        t1 = wall()
                        if not chunk:
                            continue
                        buf += chunk
                        total_len = len(buf)
                        records, buf = self._split_records(buf)
                        for end, value in records:
                            # when several records are read at once, move arrival times earlier by the bytes that follow each one (paxtest),
                            # then earlier by the record transmission time to get the first-byte time. The fixed latency is subtracted too.
                            self._emit(t1 - (total_len - end) * byte_s - rec_s - lat, value)
                    while not self._stop_evt.is_set():
                        t0 = wall()
                        if mode == "poll":
                            ser.write(cmd)
                        raw = ser.read_until(expected=term)
                        t1 = wall()
                        value = self._parse(raw)
                        if value is not None:
                            # poll mode takes the midpoint of request and response as the sample time
                            self._emit(((t0 + t1) / 2 if mode == "poll" else t1) - lat, value)
                        if mode == "poll":
                            rest = period - (wall() - t0)
                            if rest > 0:
                                self._stop_evt.wait(rest)
            except ImportError:
                self.status, self.error = "error", "pyserial not installed"
                return
            except Exception as e:  # no port/disconnected → retry after 2 s
                self.status, self.error = "error", str(e)
                self._stop_evt.wait(2.0)
        self.status = "disconnected"


class SimGauge(GaugeBase):
    """Reads the load function `load(t_wall) → N` at rate_hz with 0.1 N resolution (paxtest SimGauge).

    Without load, generates the same shape as the simulated sensor (period 4 s, 12 N peak half-wave sine) from its own start time.
    `load` can be changed while running (the GUI links/unlinks it with the simulated sensor).
    """

    def __init__(self, load: Optional[Callable[[float], float]] = None, *, noise_N: float = 0.05,
                 rate_hz: float = 10.0, seed: int = 0, clock=None, period: float = 4.0,
                 peak_N: float = 12.0) -> None:
        super().__init__(clock)
        self.load = load
        self.noise = noise_N
        self.period = 1.0 / rate_hz
        self.profile_period = period
        self.peak_N = peak_N
        self.rng = np.random.default_rng(seed + 101)
        self._t0: Optional[float] = None

    @property
    def port(self) -> str:
        return "simulation"

    def _default_load(self, t: float) -> float:
        return max(0.0, math.sin(2 * math.pi * (t - (self._t0 or t)) / self.profile_period)) * self.peak_N

    def run(self) -> None:
        self.status = "connected"
        self._t0 = self.clock.wall()
        next_t = self._t0
        while not self._stop_evt.is_set():
            now = self.clock.wall()
            if now >= next_t:
                load = self.load or self._default_load
                v = load(now) + self.rng.normal(0, self.noise)
                self._emit(now, round(v, 1))
                next_t += self.period
                if next_t < now:
                    next_t = now + self.period
            self._stop_evt.wait(max(0.0, min(0.005, next_t - self.clock.wall())))
        self.status = "disconnected"
