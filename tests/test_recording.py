"""데이터 로깅이 PXSR과 바이트 단위로 같은 CSV를 만드는지 확인한다 (계획 P3).

1. 재생 테스트: 실기 캡처를 `UsbSensor`로 재생하면서 PXSR이 기록하던 구간에 `CsvRecorder`를 켜고,
   각 행의 수신 시각에는 PXSR CSV의 `Timestamp`를 넣는다 → 결과 파일 = PXSR CSV (`cmp` 차이 0, 파일명 포함).
2. 재작성 테스트: PXSR CSV를 읽어 같은 헤더·값을 csv-writer 이식본으로 다시 쓰면 원본과 같다.
3. 헤더 테스트: PXSR CSV에 있는 모든 센서 배치(슬롯 2개, 채널 4개 등)를 `W0` 이식본이 똑같이 만든다.
"""
import itertools
import random
import re
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from paxkit.device import codec
from paxkit.device.clock import VirtualClock
from paxkit.device.frames import Frame
from paxkit.device.sim import SimUsbTransport
from paxkit.device.usb import UsbSensor
from paxkit.recording import CsvRecorder, pxsr_csv
from paxkit.recording.reader import read_log, sensor_columns

from usb_replay import CASES, run_replay  # noqa: E402

PXSR_LOG_DIR = Path.home() / "AppData/Roaming/pxsr-gen3/DataLogging"


def _parse_csv(path: Path):
    """PXSR CSV → (헤더, [(시각 문자열, [정수 | None, ...]), ...]). PXSR CSV에는 따옴표가 없다."""
    lines = path.read_bytes().split(b"\n")
    assert lines[-1] == b""
    header = lines[0].decode("utf-8").split(",")
    rows = []
    for line in lines[1:-1]:
        cells = line.decode("utf-8").split(",")
        rows.append((cells[0], [int(c) if c else None for c in cells[1:]]))
    return header, rows


def _unix(path: Path, ts: str) -> float:
    return datetime.strptime(f"{path.stem[:10]} {ts}", "%Y-%m-%d %H:%M:%S.%f").timestamp()


def _start_time(path: Path) -> datetime:
    return datetime.strptime(path.stem, "%Y-%m-%d-%H%M%S")


# ── 1. 재생 테스트 ───────────────────────────────────────────────────
@pytest.fixture(scope="module", params=CASES, ids=[c.name for c in CASES])
def replayed(request, tmp_path_factory):
    case = request.param
    tr, _, frames, _ = run_replay(case)
    # 프레임 k = 캡처의 k번째 데이터 응답 (test_usb_sensor에서 확인). 재생은 PXSR보다 빨리 요청하므로
    # 가상 시각 대신 캡처의 수신 시각으로 CSV 행과 맞춘다.
    cap_t = [st[1] + st[2][-1][0] for st in tr.steps
             if st[0][6] == codec.USB_FUNC_READ and int.from_bytes(st[0][7:11], "little") == codec.USB_ADDR_DATA
             and st[2]]
    assert len(cap_t) == len(frames)
    sessions = []   # (PXSR CSV, 첫 프레임 번호, 행마다 Unix 시각)
    for csv in sorted(case.glob("*.csv")):
        _, rows = _parse_csv(csv)
        times = [_unix(csv, ts) for ts, _ in rows]
        values = [tuple(v) for _, v in rows]
        # 행 = 연속한 프레임 (P1a에서 1:1 확인). 시각이 가까운 곳에서 값이 전부 같은 시작점을 찾는다
        starts = [j for j in range(len(frames) - len(rows) + 1)
                  if abs(cap_t[j] - times[0]) < 0.05
                  and [f.values for f in frames[j:j + len(rows)]] == values]
        assert len(starts) == 1, csv.name
        sessions.append((csv, starts[0], times))

    out = tmp_path_factory.mktemp(case.name)
    recs = {}

    def setup(sensor):
        counter = itertools.count()

        def control(frame):   # PXSR 기록 버튼을 누른 구간만 기록기에 넘긴다 (시각은 PXSR CSV 값)
            i = next(counter)
            for csv, start, times in sessions:
                if i == start:
                    recs[csv.name] = CsvRecorder(sensor, out, interval=0.05)
                    recs[csv.name].start(_start_time(csv), attach=False)
                if start <= i < start + len(times):
                    recs[csv.name].on_frame(replace(frame, t=times[i - start]))
                    if i == start + len(times) - 1:
                        recs[csv.name].stop()

        sensor.add_sink(control)

    run_replay(case, setup)
    return case, out, recs


def test_replay_csv_equals_pxsr_bytes(replayed):
    case, out, recs = replayed
    for csv in sorted(case.glob("*.csv")):
        rec = recs[csv.name]
        assert rec.path == out / csv.name   # 파일명 = rs0(시작 시각)
        assert rec.path.read_bytes() == csv.read_bytes(), csv.name
    assert sorted(p.name for p in out.glob("*.csv")) == sorted(p.name for p in case.glob("*.csv"))


def test_replay_sidecar(replayed):
    import json
    case, out, recs = replayed
    for csv in sorted(case.glob("*.csv")):
        doc = json.loads((out / csv.name).with_suffix(".json").read_text(encoding="utf-8"))
        assert doc["csv"] == csv.name
        assert doc["rows"] == recs[csv.name].line_count == len(_parse_csv(csv)[1])
        (s,) = doc["sensors"]
        assert (s["channel"], s["slot"]) == (0, 2)
        assert s["version"].startswith("PAXINI PXSR-STDDP03")


# ── 2. 재작성 테스트 ─────────────────────────────────────────────────
def _pxsr_logs():
    """설치 PC의 PXSR 기록 중 헤더 배치마다 가장 작은 파일 하나."""
    if not PXSR_LOG_DIR.is_dir():
        return []
    best = {}
    for p in PXSR_LOG_DIR.glob("*.csv"):
        with open(p, "rb") as f:
            head = f.readline()
        if p.stat().st_size and (head not in best or p.stat().st_size < best[head].stat().st_size):
            best[head] = p
    return sorted(best.values())


REWRITE_FILES = sorted(p for c in CASES for p in c.glob("*.csv")) + _pxsr_logs()


@pytest.mark.parametrize("src", REWRITE_FILES, ids=[f"{p.parent.name}/{p.name}" for p in REWRITE_FILES])
def test_rewrite_equals_original(src, tmp_path):
    header, rows = _parse_csv(src)
    w = pxsr_csv.CsvFileWriter(tmp_path / src.name, header)
    rng = random.Random(src.name)
    records = [[ts] + vals for ts, vals in rows]
    i = 0
    while i < len(records):   # 200 ms flush처럼 여러 번에 나눠 덧붙인다
        n = rng.randint(1, 40)
        w.write_records(records[i:i + n])
        i += n
    assert (tmp_path / src.name).read_bytes() == src.read_bytes()


# ── 3. 헤더(W0) ──────────────────────────────────────────────────────
def _fake_frame(taxels: int) -> Frame:
    return Frame(t=0.0, channel=0, slot=0, sensor="?", combine=(0, 0, 0), grid=(0,) * (3 * taxels))


HEADER_FILES = REWRITE_FILES


@pytest.mark.parametrize("src", HEADER_FILES, ids=[f"{p.parent.name}/{p.name}" for p in HEADER_FILES])
def test_header_layout_equals_pxsr(src):
    """PXSR 헤더에서 센서 배치만 뽑아 `v.value` 모양을 만들면 `W0` 이식본이 같은 헤더를 만든다."""
    with open(src, "rb") as f:
        line = f.readline()
    header = line.rstrip(b"\n").decode("utf-8").split(",")
    sensors = []
    for (ch, slot), cols in sensor_columns(header).items():
        while len(sensors) <= ch:
            sensors.append([])
        sensors[ch].extend([None] * (slot + 1 - len(sensors[ch])))
        sensors[ch][slot] = _fake_frame(cols.n_taxels)
    built = pxsr_csv.build_header(sensors)
    assert pxsr_csv.join_records([pxsr_csv.csv_line(built)]).encode("utf-8") == line


def test_header_and_row_skip_holes():
    a, b = _fake_frame(2), replace(_fake_frame(1), combine=(1, -2, 3), grid=(4, None, 6))
    sensors = [[None, a], [], [b]]
    assert pxsr_csv.build_header(sensors) == [
        "Timestamp",
        "0-1-1x1-X", "0-1-1x1-Y", "0-1-1x1-Z",
        "0-1-NxN-X[0]", "0-1-NxN-Y[0]", "0-1-NxN-Z[0]", "0-1-NxN-X[1]", "0-1-NxN-Y[1]", "0-1-NxN-Z[1]",
        "2-0-1x1-X", "2-0-1x1-Y", "2-0-1x1-Z", "2-0-NxN-X[0]", "2-0-NxN-Y[0]", "2-0-NxN-Z[0]",
    ]
    row = pxsr_csv.build_row("01:02:03.004", sensors)
    assert pxsr_csv.csv_line(row) == "01:02:03.004," + ",".join(["0"] * 9) + ",1,-2,3,4,,6"
    # taxel 값 개수가 3의 배수가 아니면 열은 올림 (`o2 < length/3`)
    odd = replace(_fake_frame(0), grid=(1, 2, 3, 4))
    assert pxsr_csv.build_header([[odd]])[-3:] == ["0-0-NxN-X[1]", "0-0-NxN-Y[1]", "0-0-NxN-Z[1]"]


# ── 형식 함수 ────────────────────────────────────────────────────────
def test_filename_and_timestamp():
    assert pxsr_csv.log_filename(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02-030405.csv"
    t = datetime(2026, 10, 4, 23, 6, 51, 99000).timestamp()
    assert pxsr_csv.format_timestamp(t) == "23:06:51.099"
    assert pxsr_csv.format_timestamp(t + 0.00099) == "23:06:51.099"   # ms는 내림
    assert pxsr_csv.format_timestamp(t + 0.001) == "23:06:51.100"
    for ms in range(1000):   # float 오차로 ms가 내려가지 않는다
        tt = datetime(2026, 10, 4, 0, 0, 0).timestamp() + ms / 1000
        assert pxsr_csv.format_timestamp(tt).endswith(f".{ms:03d}")


def test_stringify_field_rules():
    f = pxsr_csv.stringify_field
    assert [f(None), f(""), f(0), f(-12), f(True)] == ["", "", "0", "-12", "true"]
    assert [f("a,b"), f('a"b'), f("a\nb"), f("a\rb")] == ['"a,b"', '"a""b"', '"a\nb"', "a\rb"]
    with pytest.raises(TypeError):
        f(1.5)


# ── 기록기 동작 ──────────────────────────────────────────────────────
def _sim_sensor():
    clock = VirtualClock(wall_base=time.time())
    tr = SimUsbTransport("S1813E", 3, clock=clock)
    s = UsbSensor(tr, clock=clock, specification="S1813E")
    tr.open()
    s._spawn(s._scan())
    return clock, s


def _drive(clock, s, seconds):
    until = clock.now() + seconds
    while clock.now() < until and not s._closed:
        s._tick()


def test_no_rows_no_file(tmp_path):
    clock, s = _sim_sensor()
    rec = CsvRecorder(s, tmp_path)
    rec.start()
    rec.stop()
    assert list(tmp_path.iterdir()) == []


def test_file_created_at_first_flush(tmp_path):
    clock, s = _sim_sensor()
    _drive(clock, s, 1.5)
    rec = CsvRecorder(s, tmp_path, interval=3600)   # 주기 flush가 오지 않게
    path = rec.start(datetime(2026, 1, 2, 3, 4, 5))
    _drive(clock, s, 0.5)
    assert rec.line_count > 10 and not path.exists()
    rec.stop()
    header, rows = _parse_csv(path)
    assert path.name == "2026-01-02-030405.csv"
    assert header[1:4] == ["0-2-1x1-X", "0-2-1x1-Y", "0-2-1x1-Z"] and len(header) == 1 + 3 + 31 * 3
    assert len(rows) == rec.line_count
    assert path.with_suffix(".json").is_file()


def test_periodic_flush_appends(tmp_path):
    clock, s = _sim_sensor()
    _drive(clock, s, 1.5)
    rec = CsvRecorder(s, tmp_path, interval=0.02)
    path = rec.start()
    _drive(clock, s, 0.2)
    deadline = time.monotonic() + 2
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists() and rec.active   # 정지 전에 파일이 생긴다
    _drive(clock, s, 0.2)
    rec.stop()
    _, rows = _parse_csv(path)
    assert len(rows) == rec.line_count
    assert path.read_bytes().count(b"Timestamp") == 1


def test_disconnect_stops_recording(tmp_path):
    """PXSR `s1`: 연결 해제를 시작하면 기록을 먼저 멈춘다 (`o.value&&G1()`)."""
    clock, s = _sim_sensor()
    _drive(clock, s, 1.5)
    rec = CsvRecorder(s, tmp_path)
    path = rec.start()
    _drive(clock, s, 0.5)
    s.disconnect()
    _drive(clock, s, 0.01)
    assert not rec.active
    n = rec.line_count
    _drive(clock, s, 2.0)
    assert s._closed and s.sensors == []
    assert rec.line_count == n and len(_parse_csv(path)[1]) == n


# ── 읽기 ─────────────────────────────────────────────────────────────
def test_reader_on_fixture():
    src = sorted(CASES[0].glob("*.csv"))[0]
    d = read_log(src)
    header, rows = _parse_csv(src)
    assert d.header == header and list(d.sensors) == [(0, 2)]
    assert d.t[0] == pytest.approx(_unix(src, rows[0][0]), abs=1e-6)
    assert d.force_raw((0, 2)).tolist()[5] == rows[5][1][:3]
    assert d.taxel_raw((0, 2)).shape == (len(rows), 31)


def test_reader_midnight(tmp_path):
    p = tmp_path / "2026-01-01-235959.csv"
    p.write_bytes(b"Timestamp,0-2-1x1-X,0-2-1x1-Y,0-2-1x1-Z\n23:59:59.900,1,2,3\n00:00:00.100,4,5,6\n")
    d = read_log(p)
    assert d.t[1] - d.t[0] == pytest.approx(0.2)
    assert re.match(r"^2026-01-02", datetime.fromtimestamp(d.t[1]).isoformat())
