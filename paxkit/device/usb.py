"""USB 직결 센서 리더 — PXSR 메인 화면 로직 `Hh0`(444938) 안의 USB 부분을 1:1로 옮긴다.

| PXSR (offset)                | 여기                    | 하는 일 |
|------------------------------|-------------------------|---------|
| `V0` 연결 (452526)           | `run()` 시작부          | 포트 열기 → `P1` |
| `P1` (453254)                | `_scan()`               | serviceID 0..8 버전 조회 (0.1 s 간격) → 데이터 폴링 시작 |
| `y0` 수신 처리 (448712)      | `_handle()`             | `parseUsbData` 결과로 프레임 기록·다음 요청 |
| `O3` 캘리브레이션 (454624)   | `_calibrate()`          | 폴링 중지 → 0.5 s → `setCalibration` |
| `s1` 연결 해제 (452850)      | `_close()`              | 기록 중지 → 0.08 s → 0.8 s → 포트 닫기 |
| `K` 센서 타입 변경 (456723)  | `_set_type()`           | taxel 수(`E`) 갱신 |

PXSR은 JS 이벤트 루프 하나에서 돌아간다 (`m2(t)` = `setTimeout(t*1000)`, 338149).
여기서도 스레드 하나가 수신·타이머·외부 요청을 차례로 처리해 실행 순서를 같게 한다.
`await m2(t)` 자리는 제너레이터의 `yield t`로 옮겼다.

PXSR 코드가 이상해 보여도 고치지 않는다 (CLAUDE.md 최우선 원칙). 예:
- 응답이 오지 않거나 파싱이 실패하면 다음 요청을 보내지 않아 폴링이 멈춘다 (재시도 없음).
  여기서도 다시 요청하지 않는다. 대신 PXSR에 없는 감시만 더했다 (2026-10-05 사용자 결정): 첫 프레임 뒤
  `stall_s`초 넘게 프레임이 없으면 `stalled` 이벤트를 내고 기록(sink)을 멈춘다 → 기록 파일은 멈춘 시점까지.
  센서로 가는 명령은 바뀌지 않는다. 프레임이 다시 오면 (예: 캘리브레이션 응답으로 폴링 재개) `resumed`.
- 아무 센서도 버전에 응답하지 않으면 serviceID 1로, 마지막으로 쓴 센서 타입의 taxel 수로 폴링한다.
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
TICK_S = 0.001   # 수신 확인 간격. 타이머는 이보다 짧게도 깨어난다
STALL_S = 1.0    # 이보다 오래 프레임이 없으면 수신 멈춤 (PXSR에 없음). 캘리브레이션 중 폴링 중지는 약 0.5 s


def initial_sensor_type(specification: str) -> codec.SensorType:
    """화면 생성 시 `K(o8.find(label == 저장된 specification) || o8[0])` (458385)."""
    for t in codec.SENSOR_TYPES:
        if t.label == specification:
            return t
    return codec.SENSOR_TYPES[0]


class UsbSensor(threading.Thread):
    """`start()` = PXSR 연결 버튼, `disconnect()` = 연결 해제 버튼, `calibrate()` = 캘리브레이션 버튼.

    sink는 PXSR의 데이터 로깅 경로에 해당한다: 1008 응답이 성공할 때마다 Frame 1개.
    연결 해제를 시작하면 PXSR처럼 기록을 먼저 멈추므로(`G1`) sink 호출도 그때 멈추고
    sink의 `on_stop`을 부른다 (화면 버퍼는 계속).
    """

    def __init__(self, transport, *, clock=None, specification: str = "S1813E",
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 stall_s: Optional[float] = STALL_S) -> None:
        super().__init__(daemon=True, name="UsbSensor")
        self.transport = transport
        self.clock = clock or RealClock()
        self.on_event = on_event
        self.buffer = TimeSeriesBuffer(maxlen=120_000, ncols=3)   # combineForce raw (화면용)
        self._sinks: List[Sink] = []
        self._listeners: List[Callable[[str, dict], None]] = []
        self._on_stop: Dict[Sink, Callable[[], None]] = {}
        self._sink_lock = threading.Lock()
        self._requests: "queue.Queue[Callable[[], Optional[Iterator[float]]]]" = queue.Queue()
        self._timers: list = []
        self._seq = itertools.count()
        self._parser = codec.UsbParser()   # `usbDataView`
        self._closed = False

        # PXSR 상태 (괄호 안은 PXSR 변수)
        self.sensor_type = initial_sensor_type(specification)   # (m, d)
        self._forces = self.sensor_type.forces                  # (E)
        self._sid = 0                # 명령에 넣는 serviceID (Na0 `t`)
        self.slot = 0                # (h) 응답한 serviceID − 1
        self.version = ""            # (x.value[1]) 처음 응답한 센서의 버전 문자열
        self.infos: Dict[int, str] = {}   # (f) slot → 버전 문자열
        self._stop_polling = False   # (c0 `isStopUsbGetData`)
        self._logging = True         # (o) 기록 중 여부. 해제 시작 시 False
        # (v.value) 채널 → 슬롯 → 그 슬롯의 마지막 프레임. 기록 행·헤더를 이것으로 만든다 (`W0`, `y0`).
        # USB는 채널 0만 쓴다. 해제(`s1`) 때 비운다. 리더 스레드에서만 바꾼다.
        self.sensors: List[List[Optional[Frame]]] = []

        # 상태 표시용 (PXSR에 없음, 동작에는 영향 없음)
        self.status = "disconnected"   # disconnected | connected | error
        self.error = ""
        self.frame_count = 0
        self.header_errors = 0        # parseUsbData status 1 (헤더 오류)
        self.last_rx: Optional[float] = None
        # 수신 멈춤 감시 (PXSR에 없음, 센서로 가는 명령에는 영향 없음)
        self.stall_s = stall_s
        self.stalled = False
        self._last_rx_mono: Optional[float] = None

    # ── 외부 API (다른 스레드에서 호출) ────────────────────────────
    def add_sink(self, fn: Sink, on_stop: Optional[Callable[[], None]] = None) -> None:
        """on_stop: 연결 해제 시작(PXSR `s1`의 `G1`)이나 오류로 기록이 멈출 때 리더 스레드에서 불린다."""
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
        """on_event 외에 이벤트를 더 받는다 (캘리브레이션 결과 추적 등). 리더 스레드에서 불린다."""
        with self._sink_lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[str, dict], None]) -> None:
        with self._sink_lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    def calibrate(self) -> None:
        """PXSR 캘리브레이션 버튼. PXSR처럼 중복 클릭을 막지 않는다 (두 번 누르면 명령도 두 번)."""
        self._requests.put(self._calibrate)

    def disconnect(self) -> None:
        self._requests.put(self._close)

    @property
    def service_id(self) -> int:
        return self._sid

    # ── 이벤트 루프 ────────────────────────────────────────────────
    def run(self) -> None:
        try:
            self.transport.open()   # `await P0()`
        except Exception as e:
            self.status, self.error = "error", f"open error: {e}"
            self._event("error", message=self.error)
            return
        self.status = "connected"   # PXSR: 포트가 열리면 바로 "연결 성공"
        self._event("connected")
        self._spawn(self._scan())
        try:
            while not self._closed:
                self._tick()
        except Exception as e:   # 포트 분리 등
            log.exception("USB 리더 오류")
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
            self._spawn(self._handle(chunk))   # serialport 'data' 이벤트 → `q` → `y0`
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
        """`await m2(t)` 다음까지 실행하고, t초 뒤 이어서 실행하도록 예약한다."""
        try:
            delay = next(gen)
        except StopIteration:
            return
        heapq.heappush(self._timers, (self.clock.now() + delay, next(self._seq), gen))

    def _send(self, data: bytes) -> None:
        """`a1(D0)` = `V(D0)`: 쓰기 완료를 기다리지 않는다."""
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
                log.exception("이벤트 처리 실패")

    # ── PXSR 로직 ──────────────────────────────────────────────────
    def _get_version(self) -> bytes:
        """`getVersion`: 수신 버퍼를 비우고 명령을 만든다."""
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
            return   # 펌웨어 업그레이드(OTA) 응답. 이 레포는 OTA 명령을 보내지 않는다 (범위 밖)
        a = y.startAddress
        if a == codec.USB_ADDR_CALIBRATION:
            # PXSR은 응답 상태를 보지 않고 폴링을 재개한다. 실패 표시는 parseUsbData의
            # 기능 코드 126 + 상태 바이트 ≠ 0 일 때 J3.warning(Setting.failed) 뿐 (위 warning 이벤트).
            self._stop_polling = False
            self._event("calibration_ack", t=self.clock.wall(), status=y.status,
                        function_code=y.functionCode, failed=y.warning is not None)
            self._send(self._get_type_data())
            return
        if a == codec.USB_ADDR_SET_ID:
            # PXSR: parsedata[0] == 0 이면 성공 메시지. ID 변경 명령은 보내지 않으므로 오지 않는다.
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
        """`K` (456723): 화면용 값과 taxel 수 `E`를 바꾸고 specification을 저장한다."""
        self.sensor_type = t
        self._forces = t.forces
        self._event("sensor_type", sensor=t.label, taxels=t.forces)   # GUI가 specification 저장

    def _emit(self, parsed: dict) -> None:
        t = self.clock.wall()   # `U2().format("HH:mm:ss.SSS")` 시점
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
            except Exception:   # 기록 실패가 수신 루프를 죽이지 않도록
                log.exception("sink 실패")

    def _calibrate(self):
        """`O3` USB (454624)."""
        self._stop_polling = True
        yield 0.5
        self._send(codec.usb_set_calibration(self._sid))
        self._event("calibration_sent", t=self.clock.wall())

    def _close(self):
        """`s1` (452850). USB는 센서에 보내는 해제 명령이 없다."""
        self._stop_logging()    # `o.value&&G1()`: 기록 중이면 기록 중지
        yield 0.08
        self.slot = 0           # h.value = 0
        yield 0.8
        self._shutdown()

    def _check_stall(self) -> None:
        """첫 프레임 뒤 stall_s 넘게 프레임이 없으면 한 번 알리고 sink(기록)를 멈춘다. 해제 중이면 보지 않는다."""
        if (self.stall_s is None or self.stalled or self._last_rx_mono is None or not self._logging
                or self.clock.now() - self._last_rx_mono <= self.stall_s):
            return
        self.stalled = True
        log.warning("USB 수신 멈춤: %.1f s 동안 프레임 없음", self.clock.now() - self._last_rx_mono)
        # 이벤트 먼저 (기록기가 사이드카에 남기도록), 그다음 기록 정지
        self._event("stalled", t=self.last_rx, stall_s=self.stall_s, frames=self.frame_count)
        with self._sink_lock:
            hooks = list(self._on_stop.values())
        for fn in hooks:
            try:
                fn()
            except Exception:
                log.exception("on_stop 실패")

    def _stop_logging(self) -> None:
        self._logging = False
        with self._sink_lock:
            hooks = list(self._on_stop.values())
        for fn in hooks:
            try:
                fn()
            except Exception:
                log.exception("on_stop 실패")

    def _shutdown(self) -> None:
        self._closed = True
        try:
            self.transport.close()
        except Exception:
            log.exception("포트 닫기 실패")
        self._parser.reset()    # v0.value = Buffer.from([])
        self.version = ""
        self.infos.clear()
        self.sensors = []       # v.value = []
        self._timers.clear()
