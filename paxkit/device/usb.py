"""USB direct sensor reader — 1:1 port of the USB part of the PXSR main screen logic `Hh0` (444938).

| PXSR (offset)                   | Here                    | What it does |
|---------------------------------|-------------------------|--------------|
| `V0` connect (452526)           | start of `run()`        | open port → `P1` |
| `P1` (453254)                   | `_scan()`               | query version of serviceID 0..8 (0.1 s apart) → start data polling |
| `y0` receive handler (448712)   | `_handle()`             | record frame from the `parseUsbData` result, send next request |
| `O3` calibration (454624)       | `_calibrate()`          | stop polling → 0.5 s → `setCalibration` |
| `s1` disconnect (452850)        | `_close()`              | stop logging → 0.08 s → 0.8 s → close port |
| `K` sensor type change (456723) | `_set_type()`           | update taxel count (`E`) |

PXSR runs on a single JS event loop (`m2(t)` = `setTimeout(t*1000)`, 338149).
Here too a single thread handles receive, timers and external requests in turn, keeping the same execution order.
Each `await m2(t)` became a generator `yield t`.

Do not fix PXSR code even if it looks odd (top rule in CLAUDE.md). E.g.:
- If no response arrives or parsing fails, the next request is never sent and polling stops (no retry).
  We do not re-request either. Only a watchdog absent from PXSR was added (user decision 2026-10-05): after the first
  frame, if no frame arrives for more than `stall_s` s, emit a `stalled` event and stop logging (sinks) → the log file ends at the stall.
  Commands sent to the sensor are unchanged. If frames come back (e.g. polling resumes on a calibration response), `resumed`.
- If no sensor answers the version query, poll serviceID 1 with the taxel count of the last used sensor type.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import queue
import threading
from typing import Callable, Dict, Iterator, List, Optional

from ..buffers import TimeSeriesBuffer
from . import codec
from .clock import RealClock
from .frames import Frame

log = logging.getLogger(__name__)

Sink = Callable[[Frame], None]
TICK_S = 0.001   # receive check interval. Timers may wake earlier than this
STALL_S = 1.0    # no frame for longer than this = receive stall (not in PXSR). Polling pauses about 0.5 s during calibration


def initial_sensor_type(specification: str) -> codec.SensorType:
    """On screen creation, `K(o8.find(label == saved specification) || o8[0])` (458385)."""
    for t in codec.SENSOR_TYPES:
        if t.label == specification:
            return t
    return codec.SENSOR_TYPES[0]


class UsbSensor(threading.Thread):
    """`start()` = PXSR connect button, `disconnect()` = disconnect button, `calibrate()` = calibration button.

    A sink corresponds to PXSR's data logging path: one Frame per successful 1008 response.
    When disconnecting starts, logging stops first as in PXSR (`G1`), so sink calls stop then
    and the sink's `on_stop` is called (the display buffer keeps going).
    """

    def __init__(self, transport, *, clock=None, specification: str = "S1813E",
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 stall_s: Optional[float] = STALL_S) -> None:
        super().__init__(daemon=True, name="UsbSensor")
        self.transport = transport
        self.clock = clock or RealClock()
        self.on_event = on_event
        self.buffer = TimeSeriesBuffer(maxlen=120_000, ncols=3)   # combineForce raw (for display)
        self._sinks: List[Sink] = []
        self._listeners: List[Callable[[str, dict], None]] = []
        self._on_stop: Dict[Sink, Callable[[], None]] = {}
        self._sink_lock = threading.Lock()
        self._requests: "queue.Queue[Callable[[], Optional[Iterator[float]]]]" = queue.Queue()
        self._timers: list = []
        self._seq = itertools.count()
        self._parser = codec.UsbParser()   # `usbDataView`
        self._closed = False

        # PXSR state (PXSR variable in parentheses)
        self.sensor_type = initial_sensor_type(specification)   # (m, d)
        self._forces = self.sensor_type.forces                  # (E)
        self._sid = 0                # serviceID put in commands (Na0 `t`)
        self.slot = 0                # (h) responding serviceID − 1
        self.version = ""            # (x.value[1]) version string of the first sensor to respond
        self.infos: Dict[int, str] = {}   # (f) slot → version string
        self._stop_polling = False   # (c0 `isStopUsbGetData`)
        self._logging = True         # (o) whether logging. False once disconnect starts
        # (v.value) channel → slot → last frame of that slot. Log rows and header are built from this (`W0`, `y0`).
        # USB uses channel 0 only. Cleared on disconnect (`s1`). Modified only on the reader thread.
        self.sensors: List[List[Optional[Frame]]] = []

        # for status display (not in PXSR, no effect on behavior)
        self.status = "disconnected"   # disconnected | connected | error
        self.error = ""
        self.frame_count = 0
        self.header_errors = 0        # parseUsbData status 1 (header error)
        self.last_rx: Optional[float] = None
        # receive stall watchdog (not in PXSR, no effect on commands sent to the sensor)
        self.stall_s = stall_s
        self.stalled = False
        self._last_rx_mono: Optional[float] = None

    # ── External API (called from other threads) ─────────────────────
    def add_sink(self, fn: Sink, on_stop: Optional[Callable[[], None]] = None) -> None:
        """on_stop: called on the reader thread when logging stops at disconnect start (`G1` in PXSR `s1`) or on error."""
        with self._sink_lock:
            self._sinks.append(fn)
            if on_stop is not None:
                self._on_stop[fn] = on_stop

    def remove_sink(self, fn: Sink) -> None:
        with self._sink_lock:
            if fn in self._sinks:
                self._sinks.remove(fn)
            self._on_stop.pop(fn, None)

    def add_listener(self, fn: Callable[[str, dict], None]) -> None:
        """Receive events in addition to on_event (e.g. tracking calibration results). Called on the reader thread."""
        with self._sink_lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[str, dict], None]) -> None:
        with self._sink_lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def calibrate(self) -> None:
        """PXSR calibration button. Like PXSR, double clicks are not blocked (two clicks send the command twice)."""
        self._requests.put(self._calibrate)

    def disconnect(self) -> None:
        self._requests.put(self._close)

    @property
    def service_id(self) -> int:
        return self._sid

    # ── Event loop ───────────────────────────────────────────────────
    def run(self) -> None:
        try:
            self.transport.open()   # `await P0()`
        except Exception as e:
            self.status, self.error = "error", f"open error: {e}"
            self._event("error", message=self.error)
            return
        self.status = "connected"   # PXSR: "connected" as soon as the port opens
        self._event("connected")
        self._spawn(self._scan())
        try:
            while not self._closed:
                self._tick()
        except Exception as e:   # port unplugged, etc.
            log.exception("USB reader error")
            self.status, self.error = "error", str(e)
            self._event("error", message=self.error)
            self._stop_logging()
            self._shutdown()
            return
        self.status = "disconnected"
        self._event("disconnected")

    def _tick(self) -> None:
        while True:
            try:
                req = self._requests.get_nowait()
            except queue.Empty:
                break
            self._spawn(req())
        if self._closed:
            return
        chunk = self.transport.read_available()
        if chunk:
            self._spawn(self._handle(chunk))   # serialport 'data' event → `q` → `y0`
        now = self.clock.now()
        while self._timers and self._timers[0][0] <= now and not self._closed:
            _, _, gen = heapq.heappop(self._timers)
            self._resume(gen)
        if self._closed:
            return
        self._check_stall()
        wait = TICK_S
        if self._timers:
            wait = min(wait, max(self._timers[0][0] - self.clock.now(), 0.0))
        self.clock.sleep(wait)

    def _spawn(self, gen) -> None:
        if gen is not None:
            self._resume(gen)

    def _resume(self, gen: Iterator[float]) -> None:
        """Run up to the next `await m2(t)` and schedule resumption t s later."""
        try:
            delay = next(gen)
        except StopIteration:
            return
        heapq.heappush(self._timers, (self.clock.now() + delay, next(self._seq), gen))

    def _send(self, data: bytes) -> None:
        """`a1(D0)` = `V(D0)`: does not wait for the write to complete."""
        if self._closed:
            return
        self.transport.write(data)

    def _event(self, kind: str, **info) -> None:
        with self._sink_lock:
            fns = ([self.on_event] if self.on_event is not None else []) + list(self._listeners)
        for fn in fns:
            try:
                fn(kind, dict(info))
            except Exception:
                log.exception("event handler failed")

    # ── PXSR logic ───────────────────────────────────────────────────
    def _get_version(self) -> bytes:
        """`getVersion`: clear the receive buffer and build the command."""
        self._parser.reset()
        return codec.usb_get_version(self._sid)

    def _get_type_data(self) -> bytes:
        return codec.usb_get_type_data(self._sid, self._forces)

    def _scan(self):
        """`P1` (453254)."""
        self._stop_polling = False
        for sid in codec.USB_SCAN_IDS:
            self._sid = sid            # G(D0)
            self.infos[sid] = ""       # f.value[D0] = ""
            self._send(self._get_version())
            yield 0.1
        self._sid = self.slot + 1      # G(h.value + 1)
        self._send(self._get_type_data())

    def _handle(self, chunk: bytes):
        """`y0` (448712)."""
        y = self._parser.feed(chunk, self._forces)
        if y.warning:
            self._event("warning", message=y.warning)   # J3.warning(Setting.failed)
        if y.status == -1:
            return
        if y.status == 1:
            self.header_errors += 1
        if y.functionCode in (122, 120):
            return   # firmware upgrade (OTA) response. This repo never sends OTA commands (out of scope)
        a = y.startAddress
        if a == codec.USB_ADDR_CALIBRATION:
            # PXSR resumes polling without checking the response status. The only failure indication is
            # J3.warning(Setting.failed) in parseUsbData for function code 126 + status byte ≠ 0 (warning event above).
            self._stop_polling = False
            self._event("calibration_ack", t=self.clock.wall(), status=y.status,
                        function_code=y.functionCode, failed=y.warning is not None)
            self._send(self._get_type_data())
            return
        if a == codec.USB_ADDR_SET_ID:
            # PXSR: success message if parsedata[0] == 0. Never arrives since no ID change command is sent.
            self._event("set_id_ack", status=y.parsedata[0])
            self._send(self._get_type_data())
            return
        if a == codec.USB_ADDR_DATA:
            if y.status == 0:
                self._emit(y.parsedata[0])
            if not self._stop_polling:
                yield 0.005
                self._send(self._get_type_data())
            return
        if a == codec.USB_ADDR_VERSION:
            v2 = y.parsedata[0]
            self.infos[y.serviceID - 1] = v2   # f.value[U0-1] = v2
            if self.version == "":
                t = codec.find_sensor_type(v2)
                self.version = v2
                self.slot = y.serviceID - 1
                if t is not None and t.label != self.sensor_type.label:
                    self._set_type(t)
                self._event("version", service_id=y.serviceID, version=v2,
                            sensor=self.sensor_type.label, taxels=self._forces)
            return

    def _set_type(self, t: codec.SensorType) -> None:
        """`K` (456723): update display values and taxel count `E`, and save specification."""
        self.sensor_type = t
        self._forces = t.forces
        self._event("sensor_type", sensor=t.label, taxels=t.forces)   # GUI saves specification

    def _emit(self, parsed: dict) -> None:
        t = self.clock.wall()   # at `U2().format("HH:mm:ss.SSS")`
        f = Frame(t=t, channel=0, slot=self.slot, sensor=self.sensor_type.label,
                  combine=tuple(parsed["combineForce"]), grid=tuple(parsed["multiGrid"]))
        # `v.value[0]===void 0&&(v.value[0]=[]), v.value[0][h.value]=l1[0]`
        if not self.sensors:
            self.sensors.append([])
        row = self.sensors[0]
        if len(row) <= self.slot:
            row.extend([None] * (self.slot + 1 - len(row)))
        row[self.slot] = f
        self.frame_count += 1
        self.last_rx = t
        self._last_rx_mono = self.clock.now()
        if self.stalled:
            self.stalled = False
            self._event("resumed", t=t)
        self.buffer.append(t, f.combine)
        if not self._logging:
            return
        with self._sink_lock:
            sinks = list(self._sinks)
        for fn in sinks:
            try:
                fn(f)
            except Exception:   # so a logging failure does not kill the receive loop
                log.exception("sink failed")

    def _calibrate(self):
        """`O3` USB (454624)."""
        self._stop_polling = True
        yield 0.5
        self._send(codec.usb_set_calibration(self._sid))
        self._event("calibration_sent", t=self.clock.wall())

    def _close(self):
        """`s1` (452850). USB has no disconnect command to the sensor."""
        self._stop_logging()    # `o.value&&G1()`: stop logging if logging
        yield 0.08
        self.slot = 0           # h.value = 0
        yield 0.8
        self._shutdown()

    def _check_stall(self) -> None:
        """After the first frame, if no frame for more than stall_s, notify once and stop sinks (logging). Not checked while disconnecting."""
        if (self.stall_s is None or self.stalled or self._last_rx_mono is None or not self._logging
                or self.clock.now() - self._last_rx_mono <= self.stall_s):
            return
        self.stalled = True
        log.warning("USB receive stalled: no frame for %.1f s", self.clock.now() - self._last_rx_mono)
        # event first (so the recorder can note it in the sidecar), then stop logging
        self._event("stalled", t=self.last_rx, stall_s=self.stall_s, frames=self.frame_count)
        with self._sink_lock:
            hooks = list(self._on_stop.values())
        for fn in hooks:
            try:
                fn()
            except Exception:
                log.exception("on_stop failed")

    def _stop_logging(self) -> None:
        self._logging = False
        with self._sink_lock:
            hooks = list(self._on_stop.values())
        for fn in hooks:
            try:
                fn()
            except Exception:
                log.exception("on_stop failed")

    def _shutdown(self) -> None:
        self._closed = True
        try:
            self.transport.close()
        except Exception:
            log.exception("port close failed")
        self._parser.reset()    # v0.value = Buffer.from([])
        self.version = ""
        self.infos.clear()
        self.sensors = []       # v.value = []
        self._timers.clear()
