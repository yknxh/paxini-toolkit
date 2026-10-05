"""Serial port (pyserial). The reader uses only `open / read_available / write / close`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import serial
from serial.tools import list_ports

from .codec import USB_BAUDRATE

# WCH CH343 USB-serial seen when the sensor is connected directly over USB (confirmed 2026-10-04)
CH343_VID_PID = (0x1A86, 0x55D3)


@dataclass
class PortInfo:
    device: str
    description: str
    is_sensor: bool   # True if CH343 (not verified by sending commands)


def list_serial_ports() -> List[PortInfo]:
    """Port list. Sensor candidates are marked by VID:PID only, so no command absent from PXSR is sent."""
    out = []
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        out.append(PortInfo(p.device, p.description or "", (p.vid, p.pid) == CH343_VID_PID))
    return out


def default_sensor_port() -> Optional[str]:
    cands = [p.device for p in list_serial_ports() if p.is_sensor]
    return cands[0] if len(cands) == 1 else None


class SerialTransport:
    """Opens with the same settings as PXSR `Zn0` (Node serialport): 921600 8N1, no flow control.

    Modem lines: in the PXSR capture, after opening the port the CH343 state was DTR on, RTS off (0xA4 wValue 0xDF).
    pyserial turns RTS on by default, so RTS is turned off before opening.
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
