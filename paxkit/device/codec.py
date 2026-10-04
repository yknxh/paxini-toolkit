"""PXSR 프로토콜 명령 생성·응답 파싱 (순수 함수, 시리얼 없음).

PXSR v1.0.7 렌더러 번들 `dist/index.3bcb906d.js`를 1:1로 옮긴다. 출처 offset은 그 파일의 바이트 위치.
PXSR 코드가 이상해 보여도 고치지 않는다 (CLAUDE.md 최우선 원칙).

지금은 USB 직결(`Na0`) 부분만 있다. HAND(`ka0`)는 P1b 이후 추가.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# ── 센서 타입 표 `o8` (~410477): 버전 문자열에 name이 들어 있는 첫 항목을 고른다 ──
@dataclass(frozen=True)
class SensorType:
    label: str
    text: str
    module: int
    name: str
    forces: int   # taxel 수


SENSOR_TYPES: List[SensorType] = [
    SensorType("M2826", "DP-M2826", 6, "PXSR-STDDP03B", 127),
    SensorType("L3530", "DP-L3530", 6, "PXSR-STDDP03A", 135),
    SensorType("L5325", "CP-L5325", 6, "PXSR-STDCP03A", 239),
    SensorType("S1813", "DP-S1813", 6, "PXSR-STDDP03D", 51),
    SensorType("S3013", "DP-S3013", 6, "PXSR-STDDP03E", 96),
    SensorType("S2716", "DP-S2716", 6, "PXSR-STDDP03C", 116),
    SensorType("M2324", "IP-M2324", 6, "PXSR-STDIP03A", 68),
    SensorType("M3025", "CP-M3025", 6, "PXSR-STDCP03B", 77),
    SensorType("S1813E", "DP-S1813", 6, "PXSR-STDDP03F", 31),
    SensorType("S2015", "DP-S2015", 6, "PXSR-STDDP03G", 52),
    SensorType("S1610", "IP-S1610", 6, "PXSR-STDIP03B", 25),
    SensorType("M2020", "MC-M2020", 8, "PXSR-STDMC03A", 9),
]

# PXSR 화면의 기본 taxel 수 `E=f0(239)` (~445300). 버전 응답으로 타입을 찾기 전까지 쓰인다.
DEFAULT_FORCES = 239


def find_sensor_type(version: str) -> Optional[SensorType]:
    """`o8.find(T1 => v2.includes(T1.name))` (~449700)."""
    for t in SENSOR_TYPES:
        if t.name in version:
            return t
    return None


# ── 공통 ─────────────────────────────────────────────────────────
def checksum(data) -> int:
    """`ti` (~339145): 앞 바이트 합의 2의 보수."""
    if not data:
        raise ValueError("Invalid buffer: buffer cannot be empty")
    o = 0
    for b in data:
        o = (o + (b & 255)) & 255
    return (~o + 1) & 255


def _a2(value: int, n: int = 4) -> List[int]:
    """`A2` (~339049): n=2면 UInt16LE, 아니면 UInt32LE."""
    return list(value.to_bytes(2 if n == 2 else 4, "little"))


def _int8(b: Optional[int]) -> int:
    """JS `b << 24 >> 24`. `undefined`(None)는 JS에서 0이 된다."""
    if b is None:
        return 0
    return b - 256 if b >= 128 else b


def _at(buf: bytes, i: int) -> Optional[int]:
    """JS `Buffer[i]`: 범위 밖이면 `undefined`(None)."""
    return buf[i] if i < len(buf) else None


# ── USB 직결 `Na0` (~412891) ─────────────────────────────────────
USB_BAUDRATE = 921600
USB_HEADER = [85, 170]   # cmdHeader `e`
USB_FUNC_READ = 251
USB_FUNC_WRITE = 121
USB_ADDR_CALIBRATION = 3
USB_ADDR_SET_ID = 35
USB_ADDR_DATA = 1008
USB_ADDR_VERSION = 6100
USB_SCAN_IDS = range(9)  # 연결 시 버전 조회하는 serviceID 0..8 (캡처 2026-10-04 확인)


def usb_get_version(service_id: int) -> bytes:
    """`getVersion`."""
    d = [*USB_HEADER, 9, 0, service_id, 0, 251, 212, 23, 0, 0, 100, 0]
    d.append(checksum(d))
    return bytes(d)


def usb_get_type_data(service_id: int, forces: int) -> bytes:
    """`getTypeData(d)`: addr 1008, n = 3 + 27 + d*3."""
    m = [*USB_HEADER, 9, 0]
    m += [service_id, 0, 251, *_a2(1008, 4), *_a2(3 + 27 + forces * 3, 2)]
    m.append(checksum(m))
    return bytes(m)


def usb_set_calibration(service_id: int) -> bytes:
    """`setCalibration`: addr 3에 1을 쓴다."""
    d = [*USB_HEADER, 10, 0]
    d += [service_id, 0, 121, *_a2(3, 4), *_a2(1, 2), 1]
    d.append(checksum(d))
    return bytes(d)


@dataclass
class UsbParsed:
    """`parseUsbData`의 반환값 `y`."""
    serviceID: int = 0
    frameLength: int = 0
    functionCode: int = 0
    startAddress: int = 0
    parsedata: list = field(default_factory=list)
    status: int = 0
    warning: Optional[str] = None   # PXSR이 J3.warning(Setting.failed)를 띄우는 경우


class UsbParser:
    """`parseUsbData(d, m)`: 받은 조각을 버퍼(`usbDataView`)에 이어 붙이고 프레임 하나를 해석한다.

    status: -1 = 아직 덜 받음(버퍼 유지), 0 = 성공, 1 = 실패. 성공·실패 시 버퍼를 비운다.
    """

    def __init__(self) -> None:
        self.buf = b""

    def reset(self) -> None:
        """`getVersion` 호출 시 `i.value = Buffer.from([])`."""
        self.buf = b""

    def feed(self, d: bytes, m: int) -> UsbParsed:
        self.buf = self.buf + bytes(d)
        b = self.buf
        y = UsbParsed()
        if len(b) < 14:
            y.status = -1
            return y
        if b[0] != 170 or b[1] != 85:
            # console.error("帧头校验失败")
            self.buf = b""
            y.status = 1
            return y
        y.serviceID = b[4]
        y.functionCode = b[6]
        y.startAddress = int.from_bytes(b[7:11], "little", signed=True)
        if y.functionCode == 126:
            if y.startAddress == 3:
                if b[13] != 0:
                    y.warning = "Setting.failed"
            elif b[13] != 0:
                y.status = 1
            else:
                y.status = 0
            self.buf = b""
            return y
        if y.functionCode in (122, 120):
            y.status = 1 if b[13] != 0 else 0
            self.buf = b""
            return y
        y.frameLength = int.from_bytes(b[2:4], "little", signed=True)
        if len(b) != y.frameLength + 5:
            y.status = -1
            return y
        a = y.startAddress
        if a == 3:
            self.buf = b""
            y.status = 0
            return y
        if a == 35:
            y.parsedata.append(b[-2])
            self.buf = b""
            y.status = 0
            return y
        if a == 1008:
            s = b[14:len(b) - 1]
            # 프레임이 짧으면 JS는 undefined를 그대로 둔다 (None). CSV에는 빈 칸으로 쓰인다 (csv-writer).
            combine = [_int8(_at(s, 0)), _int8(_at(s, 1)), _at(s, 2)]
            w = list(s[30:30 + m * 3])
            grid = [v if (i + 1) % 3 == 0 else _int8(v) for i, v in enumerate(w)]
            y.parsedata = [{"combineForce": combine, "multiGrid": grid}]
            self.buf = b""
            return y
        if a == 6100:
            text = b[14:len(b) - 1].decode("utf-8", errors="replace")
            text = "".join(ch for ch in text if not (ord(ch) <= 0x1F or ord(ch) == 0x7F))
            y.parsedata = text.split("\n")
            self.buf = b""
            return y
        return y   # default: 버퍼를 비우지 않는다 (PXSR 그대로)
