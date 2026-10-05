"""PXSR protocol command building and response parsing (pure functions, no serial).

1:1 port of the PXSR v1.0.7 renderer bundle `dist/index.3bcb906d.js`. Source offsets are byte positions in that file.
Do not fix PXSR code even if it looks odd (top rule in CLAUDE.md).

Currently only the USB direct (`Na0`) part and the HAND (`ka0`) calibration command. Rest of HAND after P1b.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

# ── Sensor type table `o8` (~410477): pick the first entry whose name is in the version string ──
@dataclass(frozen=True)
class SensorType:
    label: str
    text: str
    module: int
    name: str
    forces: int   # taxel count


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

# Default taxel count on the PXSR screen `E=f0(239)` (~445300). Used until the type is found from the version response.
DEFAULT_FORCES = 239


def find_sensor_type(version: str) -> Optional[SensorType]:
    """`o8.find(T1 => v2.includes(T1.name))` (~449700)."""
    for t in SENSOR_TYPES:
        if t.name in version:
            return t
    return None


# ── Common ───────────────────────────────────────────────────────
def checksum(data) -> int:
    """`ti` (~339145): two's complement of the sum of the preceding bytes."""
    if not data:
        raise ValueError("Invalid buffer: buffer cannot be empty")
    o = 0
    for b in data:
        o = (o + (b & 255)) & 255
    return (~o + 1) & 255


def _a2(value: int, n: int = 4) -> List[int]:
    """`A2` (~339049): UInt16LE if n=2, otherwise UInt32LE."""
    return list(value.to_bytes(2 if n == 2 else 4, "little"))


def _int8(b: Optional[int]) -> int:
    """JS `b << 24 >> 24`. `undefined` (None) becomes 0 in JS."""
    if b is None:
        return 0
    return b - 256 if b >= 128 else b


def _at(buf: bytes, i: int) -> Optional[int]:
    """JS `Buffer[i]`: `undefined` (None) when out of range."""
    return buf[i] if i < len(buf) else None


# ── USB direct `Na0` (~412891) ───────────────────────────────────
USB_BAUDRATE = 921600
USB_HEADER = [85, 170]   # cmdHeader `e`
USB_FUNC_READ = 251
USB_FUNC_WRITE = 121
USB_ADDR_CALIBRATION = 3
USB_ADDR_SET_ID = 35
USB_ADDR_DATA = 1008
USB_ADDR_VERSION = 6100
USB_SCAN_IDS = range(9)  # serviceIDs 0..8 queried for version on connect (confirmed by capture 2026-10-04)


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
    """`setCalibration`: writes 1 to addr 3."""
    d = [*USB_HEADER, 10, 0]
    d += [service_id, 0, 121, *_a2(3, 4), *_a2(1, 2), 1]
    d.append(checksum(d))
    return bytes(d)


@dataclass
class UsbParsed:
    """Return value `y` of `parseUsbData`."""
    serviceID: int = 0
    frameLength: int = 0
    functionCode: int = 0
    startAddress: int = 0
    parsedata: list = field(default_factory=list)
    status: int = 0
    warning: Optional[str] = None   # when PXSR shows J3.warning(Setting.failed)


class UsbParser:
    """`parseUsbData(d, m)`: appends the received chunk to the buffer (`usbDataView`) and parses one frame.

    status: -1 = incomplete (buffer kept), 0 = success, 1 = failure. The buffer is cleared on success or failure.
    """

    def __init__(self) -> None:
        self.buf = b""

    def reset(self) -> None:
        """`i.value = Buffer.from([])` when `getVersion` is called."""
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
            # If the frame is short, JS leaves undefined as is (None). Written as an empty field in the CSV (csv-writer).
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
        return y   # default: buffer is not cleared (as in PXSR)


# ── HAND board `ka0` (~415602) — calibration command only for now (reader after P1b) ──
HAND_HEADER = [85, 170]   # `t`


def hand_checksum(data) -> int:
    """`$9` (~338987): `(sum & 255 ^ 255) + 1 & 255`. An empty array gives 0 without error."""
    e = 0
    for b in data:
        e = (e + b) & 255
    return ((e ^ 255) + 1) & 255


def hand_set_calibration() -> bytes:
    """`setHandCalibration` (~417809): `[...t, 0, 23, ...A2(2,2), ...A2(1,2), 1]` + `$9`. No serviceID."""
    m = [*HAND_HEADER, 0, 23, *_a2(2, 2), *_a2(1, 2), 1]
    m.append(hand_checksum(m))
    return bytes(m)
