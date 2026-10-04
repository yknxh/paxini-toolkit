"""USB 리더(`UsbSensor`)가 PXSR과 같은 명령을 같은 순서·간격으로 보내는지 확인한다 (계획 P2).

캡처 재생: 캡처의 PXSR 명령 순서를 기대값으로 두고, 우리 리더가 보낸 명령이 다음 기대 명령과 같으면
캡처에 있던 센서 응답 조각을 캡처와 같은 지연으로 돌려준다. 가상 시계라 실제로 기다리지 않는다.
→ 리더가 보낸 명령 전체가 PXSR이 보낸 명령 전체와 바이트 단위로 같아야 한다.
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
    # 마지막 응답 뒤 PXSR 코드대로 5 ms 뒤 요청 하나를 더 보낸다 (캡처는 포트를 닫으며 끝남)
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
    # P1: 버전 조회 0.1 s 간격, 마지막 대기 뒤 첫 데이터 요청
    for k in range(10):
        assert t[k] == pytest.approx(0.1 * k, abs=1e-9)
    # y0: 데이터 응답을 받은 뒤 5 ms 뒤 다음 요청 (응답 확인 간격 1 ms만큼 늦을 수 있음)
    done = dict(tr.rx_done)
    n_checked = 0
    for k in range(9, len(t) - 1):
        nxt = tr.written[k + 1][1]
        if k in done and tr.written[k][1][6] == nxt[6] == codec.USB_FUNC_READ and (k + 1) not in calls:
            assert 0.005 - 1e-9 <= t[k + 1] - done[k] <= 0.005 + 1e-9, k
            n_checked += 1
    assert n_checked > 300
    # O3: 버튼 → 0.5 s 뒤 setCalibration (버튼 요청은 다음 확인 주기에 처리)
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
    """가상 시계로 until 시각까지 리더 루프를 돌린다."""
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
    """버전 응답이 없으면 PXSR처럼 serviceID 1, 저장된 타입의 taxel 수로 요청하고 멈춘다."""
    clock, tr, s = _sim("S1813E", service_id=12, specification="S2015")   # 0..8 밖이라 응답 없음
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
    # 캘리브레이션 직전 요청의 응답 뒤로는 요청이 없고, ack 직후 폴링 재개
    assert sent[i][0] - sent[i - 1][0] >= 0.49   # 버튼 직전 대기 중이던 요청 하나는 나갈 수 있다 (PXSR 동일)
    assert sent[i + 1][1] == codec.usb_get_type_data(3, 31)
    assert sent[i + 1][0] - sent[i][0] == pytest.approx(tr.response_delay, abs=0.0011)
    # 해제: 기록(sink)은 바로 멈추고, 폴링은 0.88 s 동안 계속되다 포트를 닫는다
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
