"""Calibration (plan P4).

Command bytes, order and the 0.5 s wait matching the PXSR capture are checked by the capture replay test (`test_usb_sensor.py`).
Here, outcome judgment (same rules as the PXSR screen), run history and sidecar notes are checked with the simulated sensor + virtual clock.
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
    """Sensor that does not answer the calibration command."""

    def _respond(self, d):
        if d[6] == codec.USB_FUNC_WRITE:
            return None
        return super()._respond(d)


class FailAckTransport(SimUsbTransport):
    """Sensor that answers with function code 126 + status 1 (the case where PXSR shows a Setting.failed warning)."""

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
    _drive(clock, s, 1.0)            # near the simulated press peak (4 s period)
    run = CalibrationRun(s, {"port": "sim"}).start()
    _drive(clock, s, 3.0, run)
    r = run.result
    assert r.outcome == "ack" and r.status == 0 and r.function_code == codec.USB_FUNC_WRITE and not r.failed
    assert len(_cal_sent(tr)) == 1
    assert r.sent - r.requested == pytest.approx(0.5, abs=0.0011)   # O3: m2(.5)
    assert r.acked - r.sent == pytest.approx(tr.response_delay, abs=0.0011)
    # the simulated sensor takes the value at receipt as zero → after value drops (the app subtracts nothing)
    assert r.before[2] > 50 and r.after[2] < r.before[2] / 4
    assert r.info["sensor"] == "S1813E" and r.info["service_id"] == 3 and r.info["port"] == "sim"
    assert s._listeners == []


def test_no_ack_stops_polling_like_pxsr():
    clock, tr, s = _sim(NoAckTransport)
    _drive(clock, s, 1.0)
    run = CalibrationRun(s).start()
    _drive(clock, s, 6.0, run)
    assert run.result.outcome == "no_ack" and run.result.acked is None
    # PXSR: c0 (isStopUsbGetData) stays true → polling never resumes (no retry)
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
    assert after and after[0] == codec.usb_get_type_data(3, 31)   # only warns; polling resumes


def test_double_press_sends_twice_like_pxsr():
    """The PXSR button does not block double clicks → two presses within 0.5 s send the command twice."""
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
    """PXSR capture replay: the captured calibration response is judged "ack" (PXSR: polling resumes with no message)."""
    runs = []

    def setup(s):
        orig = s.calibrate

        def calibrate():
            if not runs:   # when the replay tool calls s.calibrate(), start tracking the run right there
                s.calibrate = orig
                run = CalibrationRun(s)
                runs.append(run)
                run.start()
            else:
                orig()
        s.calibrate = calibrate

    tr, s, frames, calls = run_replay(case, setup)
    if not calls:
        pytest.skip("capture without calibration")
    run = runs[0]
    run.poll(now=run.result.acked + 1.0 if run.result.acked else None)
    assert run.result.outcome == "ack" and run.result.status == 0
