"""Force gauge 리더 (paxtest `devices/gauge.py`에서 이전, 계획 P5).

실장비(SerialGauge)는 config.yaml 의 gauge 섹션을 따른다.
poll/stream 두 방식과 정규식 파싱으로 일반화했다.
실측(COM7, FTDI): 2400 baud 8N1, 명령 없이 약 10 Hz 로 고정폭 6글자("0000.2", "-004.9")를 구분자 없이 연속 송신.
명령(D 등)은 무시하고 송신 속도도 바뀌지 않는다.
→ mode: stream, line_terminator: "" 이면 record_regex 로 레코드를 잘라 읽는다.

paxtest와 다른 점 (게이지는 PXSR 대체 대상이 아니므로 바이트 동일성 원칙과 무관):
- 수신 시각을 센서 리더와 같은 시계(`clock.wall()`)로 찍는다. Windows `time.time()`은 15.6 ms 단위라
  센서(정밀 시각)와 섞으면 시각이 어긋난다. 파싱·도착 시각 보정 규칙은 그대로.
- 시리얼 포트를 여는 함수(`open_serial`)를 바꿀 수 있게 했다 (장비 없이 테스트).
- SimGauge는 paxtest SimWorld 대신 하중 함수(`load`)를 받는다. 시뮬레이션 센서와 같은 하중을 주면 겹쳐 볼 수 있다.
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

    # ── sinks (세션 기록기가 붙는다) ──
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
                except Exception:  # 기록 실패가 읽기 루프를 죽이지 않도록
                    log.exception("gauge sink failed")

    def latest(self) -> Optional[float]:
        _, v = self.buffer.latest()
        return None if v is None else float(v[0])

    def receiving(self, window: float = 2.0) -> bool:
        """연결됐고 최근 window 초 안에 값이 왔는가 (paxtest `hub.gauge_receiving`)."""
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
        # 구분자 없이 값이 이어 붙어 오는 stream (예: "0000.0-000.3-004.9...") 용
        self.record_regex = re.compile(cfg.get("record_regex", r"[-+ \d]\d{3}\.\d").encode())
        self.record_len = int(cfg.get("record_len", 6))

    @property
    def port(self) -> str:
        return str(self.cfg.get("port", "COM3"))

    def _split_records(self, buf: bytes) -> tuple[list[tuple[int, float]], bytes]:
        """buf 에서 완성된 레코드를 (끝 위치, 값) 으로 꺼내고 남은 꼬리를 돌려준다.

        버퍼 끝에 걸친 레코드는 record_len 에 도달했을 때만 완성으로 본다 ("-00.7" 이 "-00.73" 의 앞부분일 수 있음).
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
            rest = max(rest, len(buf) - self.record_len)   # 매칭 안 되는 잡음은 버리되 쓰다 만 레코드는 남긴다
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
        byte_s = 10.0 / baud   # 8N1 한 바이트 전송 시간
        timeout = float(cfg.get("timeout_s", 0.2))
        wall = self.clock.wall
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
                            # 한 번에 여러 레코드가 읽히면 뒤에 남은 바이트 수만큼 도착 시각을 앞당긴다
                            self._emit(t1 - (total_len - end) * byte_s, value)
                    while not self._stop_evt.is_set():
                        t0 = wall()
                        if mode == "poll":
                            ser.write(cmd)
                        raw = ser.read_until(expected=term)
                        t1 = wall()
                        value = self._parse(raw)
                        if value is not None:
                            # poll 방식은 요청-응답 중간 시점을 샘플 시각으로 본다
                            self._emit((t0 + t1) / 2 if mode == "poll" else t1, value)
                        if mode == "poll":
                            rest = period - (wall() - t0)
                            if rest > 0:
                                self._stop_evt.wait(rest)
            except ImportError:
                self.status, self.error = "error", "pyserial 미설치"
                return
            except Exception as e:  # 포트 없음/끊김 → 2초 후 재시도
                self.status, self.error = "error", str(e)
                self._stop_evt.wait(2.0)
        self.status = "disconnected"


class SimGauge(GaugeBase):
    """하중 함수 `load(t_wall) → N`을 rate_hz, 0.1 N 분해능으로 읽는다 (paxtest SimGauge).

    load가 없으면 시뮬레이션 센서와 같은 모양(주기 4 s, 최고 12 N 반파 사인)을 자기 시작 시각부터 만든다.
    `load`는 실행 중에 바꿀 수 있다 (GUI가 시뮬레이션 센서와 연결/해제).
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
        return "시뮬레이션"

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
