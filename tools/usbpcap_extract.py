"""Extract PC↔sensor serial bytes from a USBPcap capture (.pcapng / .pcap).

Reads with pure Python, no Wireshark/tshark (so fixtures can be regenerated and verified on any OS).

Output (JSONL, one line = one USB transfer):
  {"t": receive time (unix s, as a string to keep precision), "dir": "tx"|"rx"|"ctrl", "hex": data, ...}
  - tx: PC → sensor (bulk OUT, at URB submission)
  - rx: sensor → PC (bulk IN, at URB completion)
  - ctrl: control transfer (CH343 baud rate and other settings). Includes bRequest/wValue/wIndex

Usage: python tools/usbpcap_extract.py capture.pcapng [--device 3] [-o out.jsonl]
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

# USBPcap pseudo-header (USBPCAP_BUFFER_PACKET_HEADER, packed little-endian)
_HDR = struct.Struct("<HQIHBHHBBI")  # headerLen irpId status function info bus device endpoint transfer dataLength
XFER_ISO, XFER_INTERRUPT, XFER_CONTROL, XFER_BULK = 0, 1, 2, 3


@dataclass
class UsbPacket:
    t: Decimal          # unix s
    info: int           # bit0: 1 = device→host completion (PDO→FDO)
    bus: int
    device: int
    endpoint: int       # bit7 = IN
    transfer: int
    stage: Optional[int]  # control transfer stage (0 setup, 1 data, 2 status, 3 complete)
    data: bytes


def _read_pcapng(buf: bytes) -> Iterator[Tuple[Decimal, bytes]]:
    pos = 0
    endian = "<"
    tsresol: List[Decimal] = []
    while pos + 12 <= len(buf):
        btype, blen = struct.unpack_from(endian + "II", buf, pos)
        if btype == 0x0A0D0D0A:  # Section Header Block — detect byte order
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
    """Extract only bulk data and control setup, in time order."""
    out = []
    for p in read_packets(path):
        if device is not None and p.device != device:
            continue
        from_dev = bool(p.info & 1)
        if p.transfer == XFER_BULK and p.data:
            # OUT carries data at submission (host→device), IN at completion (device→host)
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
    ap.add_argument("--device", type=int, help="USB device address (e.g. 3). All if omitted")
    ap.add_argument("-o", "--output", type=Path, help="JSONL output file (stdout if omitted)")
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
