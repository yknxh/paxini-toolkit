"""USBPcap 캡처(.pcapng / .pcap)에서 PC↔센서 시리얼 바이트를 뽑는다.

Wireshark·tshark 없이 순수 Python으로 읽는다 (fixture 재생성·검증을 어느 OS에서도 할 수 있게).

출력(JSONL, 한 줄 = USB 전송 1개):
  {"t": 수신 시각(unix 초, 문자열로 정밀도 보존), "dir": "tx"|"rx"|"ctrl", "hex": 데이터, ...}
  - tx: PC → 센서 (bulk OUT, URB 제출 시점)
  - rx: 센서 → PC (bulk IN, URB 완료 시점)
  - ctrl: control 전송 (CH343 보드레이트 등 설정). bRequest/wValue/wIndex 포함

사용: python tools/usbpcap_extract.py capture.pcapng [--device 3] [-o out.jsonl]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

# USBPcap 의사 헤더 (USBPCAP_BUFFER_PACKET_HEADER, packed little-endian)
_HDR = struct.Struct("<HQIHBHHBBI")  # headerLen irpId status function info bus device endpoint transfer dataLength
XFER_ISO, XFER_INTERRUPT, XFER_CONTROL, XFER_BULK = 0, 1, 2, 3


@dataclass
class UsbPacket:
    t: Decimal          # unix 초
    info: int           # bit0: 1 = 장치→호스트 완료(PDO→FDO)
    bus: int
    device: int
    endpoint: int       # bit7 = IN
    transfer: int
    stage: Optional[int]  # control 전송 단계 (0 setup, 1 data, 2 status, 3 complete)
    data: bytes


def _read_pcapng(buf: bytes) -> Iterator[Tuple[Decimal, bytes]]:
    pos = 0
    endian = "<"
    tsresol: List[Decimal] = []
    while pos + 12 <= len(buf):
        btype, blen = struct.unpack_from(endian + "II", buf, pos)
        if btype == 0x0A0D0D0A:  # Section Header Block — 바이트 순서 판별
            bom = struct.unpack_from("<I", buf, pos + 8)[0]
            endian = "<" if bom == 0x1A2B3C4D else ">"
            blen = struct.unpack_from(endian + "I", buf, pos + 4)[0]
            tsresol = []
        elif btype == 1:  # Interface Description Block
            res = Decimal("1e-6")
            opt = pos + 16
            end = pos + blen - 4
            while opt + 4 <= end:
                code, olen = struct.unpack_from(endian + "HH", buf, opt)
                if code == 0:
                    break
                if code == 9:  # if_tsresol
                    v = buf[opt + 4]
                    res = Decimal(2) ** -(v & 0x7F) if v & 0x80 else Decimal(10) ** -v
                opt += 4 + ((olen + 3) & ~3)
            tsresol.append(res)
        elif btype == 6:  # Enhanced Packet Block
            iface, th, tl, caplen = struct.unpack_from(endian + "IIII", buf, pos + 8)
            ts = Decimal((th << 32) | tl) * tsresol[iface]
            yield ts, buf[pos + 28: pos + 28 + caplen]
        pos += blen


def _read_pcap(buf: bytes) -> Iterator[Tuple[Decimal, bytes]]:
    magic = struct.unpack_from("<I", buf, 0)[0]
    endian = "<" if magic in (0xA1B2C3D4, 0xA1B23C4D) else ">"
    nano = magic in (0xA1B23C4D, 0x4D3CB2A1)
    frac = Decimal("1e-9") if nano else Decimal("1e-6")
    pos = 24
    while pos + 16 <= len(buf):
        sec, sub, caplen, _ = struct.unpack_from(endian + "IIII", buf, pos)
        yield Decimal(sec) + Decimal(sub) * frac, buf[pos + 16: pos + 16 + caplen]
        pos += 16 + caplen


def read_packets(path: Path) -> Iterator[UsbPacket]:
    buf = path.read_bytes()
    frames = _read_pcapng(buf) if buf[:4] == b"\x0a\x0d\x0d\x0a" else _read_pcap(buf)
    for ts, raw in frames:
        hlen, _irp, _st, _fn, info, bus, dev, ep, xfer, dlen = _HDR.unpack_from(raw, 0)
        stage = raw[_HDR.size] if xfer == XFER_CONTROL and hlen > _HDR.size else None
        yield UsbPacket(ts, info, bus, dev, ep, xfer, stage, bytes(raw[hlen: hlen + dlen]))


def extract(path: Path, device: Optional[int] = None) -> List[dict]:
    """bulk 데이터와 control setup만 시간순으로 뽑는다."""
    out = []
    for p in read_packets(path):
        if device is not None and p.device != device:
            continue
        from_dev = bool(p.info & 1)
        if p.transfer == XFER_BULK and p.data:
            # OUT은 제출(호스트→장치) 시점, IN은 완료(장치→호스트) 시점에 데이터가 실린다
            if (p.endpoint & 0x80) and from_dev:
                out.append({"t": str(p.t), "dir": "rx", "dev": p.device, "ep": p.endpoint, "hex": p.data.hex()})
            elif not (p.endpoint & 0x80) and not from_dev:
                out.append({"t": str(p.t), "dir": "tx", "dev": p.device, "ep": p.endpoint, "hex": p.data.hex()})
        elif p.transfer == XFER_CONTROL and p.stage == 0 and not from_dev and len(p.data) >= 8:
            bm, breq, wval, widx, wlen = struct.unpack_from("<BBHHH", p.data, 0)
            out.append({"t": str(p.t), "dir": "ctrl", "dev": p.device, "bmRequestType": bm, "bRequest": breq,
                        "wValue": wval, "wIndex": widx, "wLength": wlen, "hex": p.data[8:].hex()})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture", type=Path)
    ap.add_argument("--device", type=int, help="USB 장치 주소 (예: 3). 생략하면 전부")
    ap.add_argument("-o", "--output", type=Path, help="JSONL 출력 파일 (생략하면 stdout)")
    a = ap.parse_args(argv)
    rows = extract(a.capture, a.device)
    lines = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    if a.output:
        a.output.write_bytes(lines.encode("utf-8"))
    else:
        sys.stdout.write(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
