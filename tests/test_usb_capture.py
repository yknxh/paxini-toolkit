"""Verify the USB protocol reimplementation against a real capture (USBPcap) + PXSR CSV from the same period (plan P1a).

Each fixture folder:
  serial.jsonl.gz  tools/usbpcap_extract.py output (PC↔sensor bytes, receive times)
  *.csv            original CSV logged by PXSR during the same capture
"""
import datetime as dt
import gzip
import json
from pathlib import Path

import pytest

from paxkit.device import codec

FIXTURES = Path(__file__).parent / "fixtures"
CASES = sorted(p for p in FIXTURES.glob("usb_*") if (p / "serial.jsonl.gz").is_file())


def _load_serial(case: Path):
    with gzip.open(case / "serial.jsonl.gz", "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def _replay(rows):
    """Replay the captured received bytes through the PXSR parser (`parseUsbData`) in the same order."""
    parser = codec.UsbParser()
    forces = codec.DEFAULT_FORCES
    versions, frames = [], []
    for r in rows:
        data = bytes.fromhex(r["hex"])
        if r["dir"] == "tx":
            if data == codec.usb_get_version(data[4]):
                parser.reset()   # getVersion clears the buffer
            continue
        if r["dir"] != "rx":
            continue
        y = parser.feed(data, forces)
        if y.status == -1:
            continue
        if y.startAddress == codec.USB_ADDR_VERSION:
            versions.append((y.serviceID, y.parsedata[0]))
            forces = codec.find_sensor_type(y.parsedata[0]).forces
        elif y.startAddress == codec.USB_ADDR_DATA and y.status == 0:
            d = y.parsedata[0]
            frames.append((float(r["t"]), d["combineForce"] + d["multiGrid"]))
    return versions, frames


def _load_csv(path: Path):
    date = path.stem[:10]   # file name YYYY-MM-DD-HHMMSS
    out = []
    with open(path, "rb") as f:
        f.readline()
        for line in f:
            cells = line.rstrip(b"\n").decode("utf-8").split(",")
            t = dt.datetime.strptime(f"{date} {cells[0]}", "%Y-%m-%d %H:%M:%S.%f").timestamp()
            out.append((t, [int(v) for v in cells[1:]]))
    return out


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_commands_reproduced(case):
    """Every command byte sequence PXSR sent can be reproduced exactly by codec functions."""
    rows = _load_serial(case)
    versions, _ = _replay(rows)
    sid = versions[0][0]
    forces = codec.find_sensor_type(versions[0][1]).forces
    known = {codec.usb_get_version(i) for i in codec.USB_SCAN_IDS}
    known |= {codec.usb_get_type_data(sid, forces), codec.usb_set_calibration(sid)}
    tx = [bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "tx"]
    assert tx and all(b in known for b in tx)
    # connect sequence: version query for serviceID 0..8 → data polling
    assert tx[:9] == [codec.usb_get_version(i) for i in codec.USB_SCAN_IDS]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_parsed_values_match_pxsr_csv(case):
    """Every PXSR CSV row = a frame parsed from captured bytes (all columns, 1:1, no skips or duplicates)."""
    _, frames = _replay(_load_serial(case))
    csvs = sorted(case.glob("*.csv"))
    assert csvs
    for path in csvs:
        rows = _load_csv(path)
        j, used = 0, []
        for t, vals in rows:
            # frame whose reception ended just before the row time (2 ms slack for USBPcap/JS clock difference)
            while j + 1 < len(frames) and frames[j + 1][0] <= t + 0.002:
                j += 1
            assert frames[j][1] == vals, f"{path.name} {t}"
            used.append(j)
        assert used == list(range(used[0], used[0] + len(rows))), f"{path.name}: frame mapping is not 1:1"
