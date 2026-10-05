"""Check that the USB reader (`UsbSensor`) sends the same commands as PXSR in the same order and timing (plan P2).

Capture replay: the PXSR command sequence in the capture is the expectation; when our reader sends the next expected command,
the captured sensor response chunks are returned with the captured delays. Virtual clock, so no real waiting.
→ All commands sent by the reader must be byte-identical to all commands sent by PXSR.
"""
import pytest

from paxkit.device import codec
from paxkit.device.clock import VirtualClock
from paxkit.device.sim import SimUsbTransport
from paxkit.device.usb import UsbSensor

from usb_replay import CASES, run_replay  # noqa: E402


@pytest.fixture(scope="module", params=CASES, ids=[c.name for c in CASES])
def replay(request):
    return request.param, run_replay(request.param)


def test_commands_equal_capture(replay):
    _, (tr, s, _, _) = replay
    sent = [d for _, d in tr.written]
    expected = [st[0] for st in tr.steps]
    assert sent[:len(expected)] == expected
    # after the last response, one more request goes out 5 ms later per PXSR code (the capture ends by closing the port)
    assert sent[len(expected):] == [expected[-1]]
    assert s.status == "disconnected" and not tr.is_open


def test_frames_equal_capture(replay):
    _, (tr, s, frames, _) = replay
    expected = []
    for st in tr.steps:
        if st[0][6] == codec.USB_FUNC_READ and int.from_bytes(st[0][7:11], "little") == codec.USB_ADDR_DATA and st[2]:
            y = codec.UsbParser().feed(b"".join(c for _, c in st[2]), s.sensor_type.forces)
            expected.append(tuple(y.parsedata[0]["combineForce"] + y.parsedata[0]["multiGrid"]))
    assert [f.values for f in frames] == expected
    assert {f.slot for f in frames} == {2} and {f.channel for f in frames} == {0}


def test_timing_equal_pxsr_code(replay):
    _, (tr, _, _, calls) = replay
    t = [w[0] for w in tr.written]
    # P1: version queries 0.1 s apart, first data request after the last wait
    for k in range(10):
        assert t[k] == pytest.approx(0.1 * k, abs=1e-9)
    # y0: next request 5 ms after a data response (may be late by up to the 1 ms receive check interval)
    done = dict(tr.rx_done)
    n_checked = 0
    for k in range(9, len(t) - 1):
        nxt = tr.written[k + 1][1]
        if k in done and tr.written[k][1][6] == nxt[6] == codec.USB_FUNC_READ and (k + 1) not in calls:
            assert 0.005 - 1e-9 <= t[k + 1] - done[k] <= 0.005 + 1e-9, k
            n_checked += 1
    assert n_checked > 300
    # O3: button → setCalibration 0.5 s later (button request handled on the next check cycle)
    has_cal = any(st[0][6] == codec.USB_FUNC_WRITE for st in tr.steps)
    assert bool(calls) == has_cal
    for k, t_btn in calls.items():
        assert 0.5 <= t[k] - t_btn <= 0.5 + 0.0011


def _sim(sensor="S1813E", service_id=3, specification="S1813E"):
    clock = VirtualClock(wall_base=1_790_000_000.0)
    tr = SimUsbTransport(sensor, service_id, clock=clock)
    s = UsbSensor(tr, clock=clock, specification=specification)
    return clock, tr, s


def _drive(clock, s, until):
    """Run the reader loop on the virtual clock until time `until`."""
    s.transport.open()
    s._spawn(s._scan())
    while clock.now() < until and not s._closed:
        s._tick()


def test_sim_detects_sensor_type_and_slot():
    clock, tr, s = _sim("S2015", service_id=3)
    frames = []
    s.add_sink(frames.append)
    _drive(clock, s, 2.0)
    assert s.version == "PAXINI PXSR-STDDP03G-v1.0.5"
    assert s.sensor_type.label == "S2015" and s.slot == 2 and s.service_id == 3
    assert frames and all(len(f.grid) == 52 * 3 for f in frames)
    assert tr.written[9][1] == codec.usb_get_type_data(3, 52)


def test_no_response_polls_service_id_1_with_last_type():
    """Without a version response, request serviceID 1 with the saved type's taxel count and stop, as PXSR does."""
    clock, tr, s = _sim("S1813E", service_id=12, specification="S2015")   # outside 0..8, so no response
    _drive(clock, s, 3.0)
    sent = [d for _, d in tr.written]
    assert sent == [codec.usb_get_version(i) for i in range(9)] + [codec.usb_get_type_data(1, 52)]


def test_calibration_and_disconnect_sequence():
    clock, tr, s = _sim()
    frames = []
    s.add_sink(frames.append)
    _drive(clock, s, 1.5)
    s.calibrate()
    _drive_more(clock, s, 2.5)
    sent = [(t, d) for t, d in tr.written]
    cal = [i for i, (_, d) in enumerate(sent) if d == codec.usb_set_calibration(3)]
    assert len(cal) == 1
    i = cal[0]
    # no requests after the response to the request just before calibration; polling resumes right after the ack
    assert sent[i][0] - sent[i - 1][0] >= 0.49   # one request already waiting before the button may go out (same as PXSR)
    assert sent[i + 1][1] == codec.usb_get_type_data(3, 31)
    assert sent[i + 1][0] - sent[i][0] == pytest.approx(tr.response_delay, abs=0.0011)
    # disconnect: logging (sink) stops at once, polling continues for 0.88 s, then the port closes
    n_frames = len(frames)
    t_dc = clock.now()
    s.disconnect()
    _drive_more(clock, s, t_dc + 2.0)
    assert s._closed and not tr.is_open
    assert len(frames) == n_frames
    assert s.frame_count > n_frames
    assert tr.written[-1][0] - t_dc == pytest.approx(0.88, abs=0.01)


def _drive_more(clock, s, until):
    while clock.now() < until and not s._closed:
        s._tick()
