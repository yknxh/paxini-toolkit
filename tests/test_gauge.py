"""Force gauge 리더 (계획 P5). 실기 형식은 paxtest에서 확인한 값(2400 baud, 구분자 없는 6글자 N 레코드)."""
import threading
import time

import numpy as np
import pytest

from paxkit.buffers import TimeSeriesBuffer
from paxkit.config import Config
from paxkit.device.sim import SimUsbTransport
from paxkit.device.usb import UsbSensor
from paxkit.gauge import SerialGauge, SimGauge, sensor_lag, xcorr_offset

STREAM = b"0000.0-000.3-004.9-012.30000.1"
VALUES = [-0.0, 0.3, 4.9, 12.3, -0.1]   # invert: 누름('-')이 +


def _cfg(**kw):
    return {**Config.load().section("gauge"), **kw}


class StepClock:
    """wall()이 정해 둔 값을 차례로 돌려준다."""

    def __init__(self, times):
        self.times = list(times)
        self.last = 0.0

    def wall(self):
        if self.times:
            self.last = self.times.pop(0)
        return self.last


class FakeSerial:
    """read/read_until이 정해 둔 조각을 차례로 돌려주고, 다 쓰면 리더를 멈춘다."""

    def __init__(self, chunks, gauge=None):
        self.chunks = list(chunks)
        self.gauge = gauge
        self.written = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def reset_input_buffer(self):
        pass

    @property
    def in_waiting(self):
        return len(self.chunks[0]) if self.chunks else 0

    def _next(self):
        if not self.chunks:
            self.gauge.stop()
            return b""
        return self.chunks.pop(0)

    def read(self, n=1):
        return self._next()

    def read_until(self, expected=b"\r"):
        return self._next()

    def write(self, d):
        self.written.append(d)


def _run(gauge, chunks):
    out = []
    gauge.add_sink(lambda t, v: out.append((t, v)))
    ser = FakeSerial(chunks, gauge)
    gauge.open_serial = lambda port, baud, timeout: ser
    gauge.run()
    return out, ser


def test_config_matches_paxtest_measured():
    c = Config.load().section("gauge")
    assert c["baudrate"] == 2400 and c["mode"] == "stream" and c["line_terminator"] == "" and c["invert"]


@pytest.mark.parametrize("size", [1, 2, 3, 5, 6, 7, 11, len(STREAM)])
def test_stream_split_any_chunking(size):
    g = SerialGauge(_cfg())
    chunks = [STREAM[i:i + size] for i in range(0, len(STREAM), size)]
    out, _ = _run(g, chunks)
    assert [v for _, v in out] == pytest.approx(VALUES)


def test_noise_and_partial_record():
    g = SerialGauge(_cfg())
    recs, rest = g._split_records(b"\x00\xff00.0-004.9-00")
    assert [v for _, v in recs] == pytest.approx([4.9]) and rest == b"-00"
    recs, rest = g._split_records(rest + b"0.7")
    assert [v for _, v in recs] == pytest.approx([0.7]) and rest == b""


def test_stream_arrival_time_correction():
    """한 번에 읽힌 레코드는 뒤에 남은 바이트 수 × 10/baud 만큼 도착 시각을 앞당기고 (paxtest와 같음),
    레코드 전송 시간(6바이트)만큼 더 앞당겨 첫 바이트 시각으로 둔다. latency_s도 뺀다."""
    g = SerialGauge(_cfg(latency_s=0.0), clock=StepClock([100.0]))
    out, _ = _run(g, [b"0000.0-000.3"])
    byte_s = 10.0 / 2400
    assert [t for t, _ in out] == pytest.approx([100.0 - 12 * byte_s, 100.0 - 6 * byte_s])
    assert g.status == "disconnected"
    g = SerialGauge(_cfg(latency_s=0.025), clock=StepClock([100.0]))
    out, _ = _run(g, [b"0000.0"])
    assert [t for t, _ in out] == pytest.approx([100.0 - 6 * byte_s - 0.025])


def test_poll_mode_midpoint_time():
    g = SerialGauge(_cfg(mode="poll", line_terminator="\r", invert=False, poll_hz=1000, latency_s=0.0),
                    clock=StepClock([10.0, 10.02, 10.03]))
    out, ser = _run(g, [b" 12.5\r"])
    assert out == [(pytest.approx(10.01), 12.5)] and ser.written[0] == b"D\r"


def test_unit_scale():
    g = SerialGauge(_cfg(unit_scale=9.80665, invert=False))
    assert g._parse(b"0001.0") == pytest.approx(9.80665)


def test_open_error_retries_until_stop():
    g = SerialGauge(_cfg(port="NOPE"))
    calls = []

    def fail(port, baud, timeout):
        calls.append(port)
        raise OSError("no port")
    g.open_serial = fail
    g.start()
    time.sleep(0.1)
    assert g.status == "error" and "no port" in g.error and calls == ["NOPE"]
    g.stop()
    g.join(1)
    assert not g.is_alive() and g.status == "disconnected"


def test_sim_gauge_rate_and_resolution():
    g = SimGauge(lambda t: 5.04, noise_N=0.0, rate_hz=50)
    g.start()
    time.sleep(0.5)
    g.stop()
    g.join(1)
    t, v = g.buffer.snapshot()
    assert 15 <= len(t) <= 30 and set(v[:, 0]) == {5.0}
    assert g.latest() == 5.0


def test_xcorr_offset_finds_delay():
    t = np.arange(0, 10, 0.1)
    f = np.maximum(0, np.sin(2 * np.pi * t / 2.5)) * 10
    tp = np.arange(0, 10, 0.01)
    fp = np.interp(tp - 0.08, t, f)   # 센서가 80 ms 늦음
    off, r = xcorr_offset(t, f, tp, fp, 1.0, 9.0, 0.5)
    assert off == pytest.approx(0.08, abs=0.011) and r > 0.95


def test_sensor_lag_needs_movement():
    gb, sb = TimeSeriesBuffer(ncols=1), TimeSeriesBuffer(ncols=3)
    for i in range(100):
        gb.append(i * 0.1, 0.0)
        sb.append(i * 0.1, (0, 0, 0))
    assert sensor_lag(gb, sb, 10.0) == (None, 0.0)


def test_sim_sensor_and_gauge_same_clock():
    """시뮬레이션 센서와 같은 하중을 읽는 게이지: 같은 시계라 보정 없이 지연이 거의 0 (P5 완료 기준의 장비 없는 버전)."""
    tr = SimUsbTransport("S1813E", period=1.0)
    s = UsbSensor(tr)
    g = SimGauge(tr.load_N, noise_N=0.0, rate_hz=50, clock=s.clock)
    s.start()
    g.start()
    time.sleep(3.0)
    lag, r = sensor_lag(g.buffer, s.buffer, s.clock.wall(), window=3.0)
    g.stop()
    s.disconnect()
    g.join(1)
    s.join(3)
    assert lag is not None and abs(lag) < 0.03 and r > 0.9
