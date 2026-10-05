"""시뮬레이션 센서: USB 직결 센서처럼 명령에 응답하는 가짜 포트.

`UsbSensor`에 SerialTransport 대신 넣으면 장비 없이 같은 코드 경로로 GUI·기록을 돌려 볼 수 있다.
응답 프레임 모양은 실기 캡처(2026-10-04, 펌웨어 v1.0.5)를 따른다. 값은 가짜 누름 프로파일:
`period`마다 한 번 누르고(올림 20 %, 유지 35 %, 내림 20 %, 쉼 25 %), 누를 때마다 힘(최대값의 100·40·70 %)과
위치(taxel 하나를 중심으로 한 분포, 센서 점 모델 좌표 기준)가 바뀐다. 게이지 테스트(bench)를 장비 없이 돌려 보기 위한 것.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from . import codec
from .clock import RealClock
from .geometry import load_geometry

SIM_VERSIONS = {
    "S1813E": "PAXINI PXSR-STDDP03F-v1.0.5",
    "S2015": "PAXINI PXSR-STDDP03G-v1.0.5",
}


def _response(sid: int, func: int, addr: int, n: int, body: bytes) -> bytes:
    """`AA 55 | len | sid 00 | func | addr | n | 01 | body | cs` (캡처한 응답과 같은 배치)."""
    f = [0xAA, 0x55, 0, 0, sid, 0, func, *addr.to_bytes(4, "little"), *n.to_bytes(2, "little"), 1, *body]
    f[2:4] = list((len(f) + 1 - 5).to_bytes(2, "little"))
    f.append(codec.checksum(f))
    return bytes(f)


class SimUsbTransport:
    def __init__(self, sensor: str = "S1813E", service_id: int = 3, *, clock=None,
                 response_delay: float = 0.0023, period: float = 4.0, peak_raw: int = 120) -> None:
        self.version = SIM_VERSIONS[sensor]
        self.taxels = codec.find_sensor_type(self.version).forces
        self.service_id = service_id
        self.clock = clock or RealClock()
        self.response_delay = response_delay   # 실측 중앙값 2.3 ms
        self.period = period
        self.peak_raw = peak_raw
        self.is_open = False
        self.written: List[Tuple[float, bytes]] = []
        self._pending: List[Tuple[float, bytes]] = []
        self._t0: Optional[float] = None
        self.zero = 0   # 캘리브레이션을 받으면 현재 값을 영점으로 삼는 흉내
        self.geometry = load_geometry(sensor)

    def open(self) -> None:
        self.is_open = True
        self._t0 = self.clock.now()

    def close(self) -> None:
        self.is_open = False
        self._pending.clear()

    def write(self, data: bytes) -> None:
        if not self.is_open:
            raise OSError("port closed")
        now = self.clock.now()
        self.written.append((now, bytes(data)))
        resp = self._respond(bytes(data))
        if resp is not None:
            self._pending.append((now + self.response_delay, resp))

    def read_available(self) -> bytes:
        now = self.clock.now()
        out = b"".join(r for t, r in self._pending if t <= now)
        self._pending = [(t, r) for t, r in self._pending if t > now]
        return out

    def _respond(self, d: bytes) -> Optional[bytes]:
        if len(d) < 14 or d[:2] != b"\x55\xaa" or codec.checksum(d[:-1]) != d[-1] or d[4] != self.service_id:
            return None
        func, addr = d[6], int.from_bytes(d[7:11], "little")
        n = int.from_bytes(d[11:13], "little")
        if func == codec.USB_FUNC_READ and addr == codec.USB_ADDR_VERSION:
            return _response(self.service_id, func, addr, n, self.version.encode().ljust(n, b"\0"))
        if func == codec.USB_FUNC_READ and addr == codec.USB_ADDR_DATA:
            return _response(self.service_id, func, addr, n, self._payload(n))
        if func == codec.USB_FUNC_WRITE and addr == codec.USB_ADDR_CALIBRATION:
            self.zero = self._press()
            return self._cal_ack(d)
        return None

    def _cal_ack(self, d: bytes) -> bytes:
        """캡처한 캘리브레이션 응답은 명령과 같은 모양 (`aa550a00…0100 00 cs`, 상태 0)."""
        f = [0xAA, 0x55, *d[2:13], 0]
        f.append(codec.checksum(f))
        return bytes(f)

    LEVELS = (1.0, 0.4, 0.7)
    SPREAD_MM = 2.5   # 누른 자리 둘레로 taxel 값이 퍼지는 정도

    def _phase(self):
        t = self.clock.now() - (self._t0 or 0.0)
        k, u = divmod(t / self.period, 1.0)
        return int(k), u

    def _press(self) -> float:
        """지금 하중 (raw). 한 주기: 올림 0~0.2, 유지 ~0.55, 내림 ~0.75, 쉼."""
        k, u = self._phase()
        if u < 0.2:
            a = u / 0.2
        elif u < 0.55:
            a = 1.0
        elif u < 0.75:
            a = (0.75 - u) / 0.2
        else:
            a = 0.0
        return a * self.LEVELS[k % len(self.LEVELS)] * self.peak_raw

    def _center(self) -> int:
        """이번 누름의 중심 taxel (누를 때마다 센서 위를 고르게 옮겨 다닌다)."""
        k, _ = self._phase()
        return (k * 7) % self.taxels

    def load_N(self, _t_wall: float = 0.0) -> float:
        """지금 누르는 실제 하중 (N, 캘리브레이션 영점과 무관). 시뮬레이션 게이지(`SimGauge.load`)용."""
        return self._press() / 10.0 if self.is_open else 0.0

    def _payload(self, n: int) -> bytes:
        z = max(0.0, self._press() - self.zero)
        p = bytearray(n)
        nt = min(self.taxels, (n - 30) // 3)
        g = self.geometry
        if g is not None:
            c = self._center()
            nx, ny, nz = g.normals[c]
            d2 = ((g.taxels - g.taxels[c]) ** 2).sum(axis=1)
            w = [math.exp(-v / (2 * self.SPREAD_MM ** 2)) for v in d2]
        else:
            nx, ny, nz = 0.05, -0.03, 1.0
            w = [math.exp(-((i - self.taxels / 2) ** 2) / (self.taxels / 3)) for i in range(self.taxels)]
        # 합력: 누른 면의 법선 방향 (크기 ≈ 하중)
        p[0] = int(z * nx) & 0xFF                 # X (int8)
        p[1] = int(z * ny) & 0xFF                 # Y (int8)
        p[2] = min(int(z * nz), 255)              # Z (uint8)
        for i in range(nt):
            p[30 + 3 * i + 2] = min(int(z * w[i] * 0.3), 255)
        return bytes(p)
