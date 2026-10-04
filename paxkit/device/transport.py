"""시리얼 포트 (pyserial). 리더는 `open / read_available / write / close`만 쓴다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import serial
from serial.tools import list_ports

from .codec import USB_BAUDRATE

# 센서 USB 직결 시 보이는 WCH CH343 USB-시리얼 (2026-10-04 확인)
CH343_VID_PID = (0x1A86, 0x55D3)


@dataclass
class PortInfo:
    device: str
    description: str
    is_sensor: bool   # CH343이면 True (명령을 보내 확인하지는 않는다)


def list_serial_ports() -> List[PortInfo]:
    """포트 목록. PXSR에 없는 명령을 보내지 않도록 VID:PID로만 센서 후보를 표시한다."""
    out = []
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        out.append(PortInfo(p.device, p.description or "", (p.vid, p.pid) == CH343_VID_PID))
    return out


def default_sensor_port() -> Optional[str]:
    cands = [p.device for p in list_serial_ports() if p.is_sensor]
    return cands[0] if len(cands) == 1 else None


class SerialTransport:
    """PXSR `Zn0`(Node serialport) 설정과 같게 연다: 921600 8N1, 흐름 제어 없음.

    모뎀 신호: PXSR 캡처에서 포트를 연 뒤 CH343 상태가 DTR 켜짐·RTS 꺼짐(0xA4 wValue 0xDF)이었다.
    pyserial은 기본으로 RTS도 켜므로 열기 전에 RTS를 끈다.
    """

    def __init__(self, port: str, baudrate: int = USB_BAUDRATE) -> None:
        self.port = port
        self.baudrate = baudrate
        self._ser: Optional[serial.Serial] = None

    def open(self) -> None:
        s = serial.Serial()
        s.port = self.port
        s.baudrate = self.baudrate
        s.bytesize = serial.EIGHTBITS
        s.parity = serial.PARITY_NONE
        s.stopbits = serial.STOPBITS_ONE
        s.rtscts = False
        s.dsrdtr = False
        s.xonxoff = False
        s.timeout = 0
        s.write_timeout = 1.0
        s.rts = False
        s.dtr = True
        s.open()
        self._ser = s

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def read_available(self) -> bytes:
        s = self._ser
        n = s.in_waiting
        return s.read(n) if n else b""

    def write(self, data: bytes) -> None:
        self._ser.write(data)

    def close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None
