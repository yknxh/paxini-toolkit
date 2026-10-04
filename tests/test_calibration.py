"""캘리브레이션 (계획 P4).

명령 바이트·순서·0.5 s 대기가 PXSR 캡처와 같은지는 캡처 재생 테스트(`test_usb_sensor.py`)가 확인한다.
여기서는 결과 판정(PXSR 화면과 같은 규칙), 실행 기록, 사이드카 기록을 시뮬레이션 센서 + 가상 시계로 확인한다.
"""
import json

import pytest

from paxkit.calibration import CalibrationRun, append_history, read_history
from paxkit.device import codec
from paxkit.device.clock import VirtualClock
from paxkit.device.sim import SimUsbTransport
from paxkit.device.usb import UsbSensor
from paxkit.recording import CsvRecorder

from usb_replay import CASES, run_replay  # noqa: E402


class NoAckTransport(SimUsbTransport):
    """캘리브레이션 명령에 응답하지 않는 센서."""

    def _respond(self, d):
        if d[6] == codec.USB_FUNC_WRITE:
            return None
        return super()._respond(d)


class FailAckTransport(SimUsbTransport):
    """기능 코드 126 + 상태 1로 응답하는 센서 (PXSR이 Setting.failed 경고를 띄우는 경우)."""

    def _cal_ack(self, d):
        f = [0xAA, 0x55, *d[2:6], 126, *d[7:13], 1]
        f.append(codec.checksum(f))
        return bytes(f)


def _sim(transport_cls=SimUsbTransport):
    clock = VirtualClock(wall_base=1_790_000_000.0)
    tr = transport_cls("S1813E", 3, clock=clock)
    s = UsbSensor(tr, clock=clock, specification="S1813E")
    tr.open()
    s._spawn(s._scan())
    return clock, tr, s


def _drive(clock, s, until, run=None):
    while clock.now() < until and not s._closed:
        s._tick()
        if run is not None and run.poll():
            run = None


def _cal_sent(tr):
    return [t for t, d in tr.written if d == codec.usb_set_calibration(3)]


def test_ack_result_and_before_after():
    clock, tr, s = _sim()
    _drive(clock, s, 1.0)            # 시뮬레이션 누름 최고점 근처 (주기 4 s)
    run = CalibrationRun(s, {"port": "sim"}).start()
    _drive(clock, s, 3.0, run)
    r = run.result
    assert r.outcome == "ack" and r.status == 0 and r.function_code == codec.USB_FUNC_WRITE and not r.failed
    assert len(_cal_sent(tr)) == 1
    assert r.sent - r.requested == pytest.approx(0.5, abs=0.0011)   # O3: m2(.5)
    assert r.acked - r.sent == pytest.approx(tr.response_delay, abs=0.0011)
    # 시뮬레이션 센서는 받은 순간 값을 영점으로 삼는다 → 후 값이 작아진다 (앱은 아무것도 빼지 않음)
    assert r.before[2] > 50 and r.after[2] < r.before[2] / 4
    assert r.info["sensor"] == "S1813E" and r.info["service_id"] == 3 and r.info["port"] == "sim"
    assert s._listeners == []


def test_no_ack_stops_polling_like_pxsr():
    clock, tr, s = _sim(NoAckTransport)
    _drive(clock, s, 1.0)
    run = CalibrationRun(s).start()
    _drive(clock, s, 6.0, run)
    assert run.result.outcome == "no_ack" and run.result.acked is None
    # PXSR: c0(isStopUsbGetData)가 계속 true → 폴링 재개 없음 (재시도 없음)
    t_cal = _cal_sent(tr)[0]
    assert [d for t, d in tr.written if t > t_cal] == []


def test_failed_ack_warns_and_resumes_polling():
    clock, tr, s = _sim(FailAckTransport)
    events = []
    s.add_listener(lambda k, info: events.append(k))
    _drive(clock, s, 1.0)
    run = CalibrationRun(s).start()
    _drive(clock, s, 3.0, run)
    assert run.result.outcome == "failed" and run.result.function_code == 126
    assert "warning" in events
    t_cal = _cal_sent(tr)[0]
    after = [d for t, d in tr.written if t > t_cal]
    assert after and after[0] == codec.usb_get_type_data(3, 31)   # 경고만 띄우고 폴링은 재개


def test_double_press_sends_twice_like_pxsr():
    """PXSR 버튼은 중복 클릭을 막지 않는다 → 0.5 s 안에 두 번 누르면 명령도 두 번."""
    clock, tr, s = _sim()
    _drive(clock, s, 1.0)
    r1 = CalibrationRun(s).start()
    _drive(clock, s, 1.2)
    r2 = CalibrationRun(s).start()
    _drive(clock, s, 4.0)
    sent = _cal_sent(tr)
    assert len(sent) == 2
    assert r1.poll() and r2.poll()
    assert r1.result.sent == pytest.approx(sent[0] + 1_790_000_000.0)
    assert r2.result.sent == pytest.approx(sent[1] + 1_790_000_000.0)


def test_history_roundtrip(tmp_path):
    clock, tr, s = _sim()
    _drive(clock, s, 1.0)
    run = CalibrationRun(s, {"port": "sim"}).start()
    _drive(clock, s, 3.0, run)
    p = tmp_path / "history.jsonl"
    append_history(run.result, p)
    append_history(run.result, p)
    rows = read_history(p)
    assert len(rows) == 2 and rows[0]["outcome"] == "ack" and rows[0]["info"]["port"] == "sim"
    assert rows[0]["requested"].startswith("2026-")


def test_sidecar_has_calibration_event(tmp_path):
    clock, tr, s = _sim()
    rec = CsvRecorder(s, tmp_path, interval=3600)
    rec.start()
    _drive(clock, s, 1.0)
    run = CalibrationRun(s).start()
    _drive(clock, s, 3.0, run)
    d = run.result.to_dict()
    d.pop("requested")
    rec.note_event("calibration", run.result.requested, **d)
    rec.stop()
    side = json.loads(rec.path.with_suffix(".json").read_text(encoding="utf-8"))
    ev = [e for e in side["events"] if e["kind"] == "calibration"]
    assert len(ev) == 1 and ev[0]["outcome"] == "ack" and ev[0]["before"] and ev[0]["after"]


@pytest.mark.parametrize("case", [c for c in CASES], ids=[c.name for c in CASES])
def test_replay_calibration_result(case):
    """PXSR 캡처 재생: 캡처의 캘리브레이션 응답이 "ack"(PXSR: 메시지 없이 폴링 재개)로 판정된다."""
    runs = []

    def setup(s):
        orig = s.calibrate

        def calibrate():
            if not runs:   # 재생 도구가 s.calibrate()를 부르면 그 자리에서 실행 추적을 시작
                s.calibrate = orig
                run = CalibrationRun(s)
                runs.append(run)
                run.start()
            else:
                orig()
        s.calibrate = calibrate

    tr, s, frames, calls = run_replay(case, setup)
    if not calls:
        pytest.skip("캘리브레이션 없는 캡처")
    run = runs[0]
    run.poll(now=run.result.acked + 1.0 if run.result.acked else None)
    assert run.result.outcome == "ack" and run.result.status == 0
