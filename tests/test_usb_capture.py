"""실기 캡처(USBPcap) + 같은 시간대 PXSR CSV로 USB 프로토콜 재구현을 검증한다 (계획 P1a).

fixture 폴더마다:
  serial.jsonl.gz  tools/usbpcap_extract.py 출력 (PC↔센서 바이트, 수신 시각)
  *.csv            같은 캡처 동안 PXSR이 기록한 원본 CSV
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
    """캡처한 수신 바이트를 PXSR 파서(`parseUsbData`) 순서 그대로 재생한다."""
    parser = codec.UsbParser()
    forces = codec.DEFAULT_FORCES
    versions, frames = [], []
    for r in rows:
        data = bytes.fromhex(r["hex"])
        if r["dir"] == "tx":
            if data == codec.usb_get_version(data[4]):
                parser.reset()   # getVersion이 버퍼를 비운다
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
    date = path.stem[:10]   # 파일명 YYYY-MM-DD-HHMMSS
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
    """PXSR이 보낸 명령 바이트를 codec 함수로 전부 똑같이 만들 수 있다."""
    rows = _load_serial(case)
    versions, _ = _replay(rows)
    sid = versions[0][0]
    forces = codec.find_sensor_type(versions[0][1]).forces
    known = {codec.usb_get_version(i) for i in codec.USB_SCAN_IDS}
    known |= {codec.usb_get_type_data(sid, forces), codec.usb_set_calibration(sid)}
    tx = [bytes.fromhex(r["hex"]) for r in rows if r["dir"] == "tx"]
    assert tx and all(b in known for b in tx)
    # 연결 순서: serviceID 0..8 버전 조회 → 데이터 폴링
    assert tx[:9] == [codec.usb_get_version(i) for i in codec.USB_SCAN_IDS]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_parsed_values_match_pxsr_csv(case):
    """PXSR CSV의 모든 행 = 캡처 바이트를 파싱한 프레임 (모든 열, 1:1, 건너뜀·중복 없음)."""
    _, frames = _replay(_load_serial(case))
    csvs = sorted(case.glob("*.csv"))
    assert csvs
    for path in csvs:
        rows = _load_csv(path)
        j, used = 0, []
        for t, vals in rows:
            # 행 시각 직전에 수신이 끝난 프레임 (USBPcap·JS 시계 차이로 2 ms 여유)
            while j + 1 < len(frames) and frames[j + 1][0] <= t + 0.002:
                j += 1
            assert frames[j][1] == vals, f"{path.name} {t}"
            used.append(j)
        assert used == list(range(used[0], used[0] + len(rows))), f"{path.name}: 프레임 대응이 1:1이 아님"
