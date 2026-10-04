"""USB 직결 센서를 PXSR 없이 직접 읽어 본다 (PXSR은 꺼 두어야 포트를 열 수 있다).

    python tools/usb_live_check.py [COM3] [--seconds 10] [--calibrate] [--record]

연결 → 지정 시간 동안 수신 → 해제. 버전·센서 타입·수신 속도·요청 간격·최근 값을 출력한다.
--calibrate: 중간에 캘리브레이션 명령을 보낸다 (센서 영점이 바뀌므로 아무것도 누르지 않은 상태에서만).
             결과(응답·전후 합력)를 출력하고 `data/calibration/history.jsonl`에 남긴다.
--record: 수신하는 동안 `data/logs/`에 PXSR 형식 CSV로 기록하고, 행 수 = 프레임 수인지 확인한다.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paxkit.calibration import OUTCOME_TEXT, CalibrationRun, append_history  # noqa: E402
from paxkit.device.transport import SerialTransport, default_sensor_port, list_serial_ports  # noqa: E402
from paxkit.device.usb import UsbSensor  # noqa: E402
from paxkit.recording import CsvRecorder  # noqa: E402


class LoggingTransport(SerialTransport):
    """보낸 명령의 시각을 기록한다 (요청 간격 확인용)."""

    def __init__(self, port):
        super().__init__(port)
        self.tx = []

    def write(self, data):
        self.tx.append((time.perf_counter(), bytes(data)))
        super().write(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    port = a.port or default_sensor_port()
    if not port:
        print("센서 포트를 찾지 못함. 포트 목록:")
        for p in list_serial_ports():
            print(f"  {p.device}  {p.description}  {'(CH343)' if p.is_sensor else ''}")
        return 1
    tr = LoggingTransport(port)
    events = []
    s = UsbSensor(tr, on_event=lambda k, info: events.append((time.perf_counter(), k, info)))
    frames = []
    s.add_sink(frames.append)
    t0 = time.perf_counter()
    s.start()
    rec = None
    if a.record:
        rec = CsvRecorder(s, info={"mode": "usb", "port": port, "tool": "usb_live_check"})
        rec.start()   # 첫 프레임 전에 시작해도 된다 (파일은 첫 행 뒤에 생긴다)
    cal = None
    if a.calibrate:
        time.sleep(a.seconds / 2)
        cal = CalibrationRun(s, {"mode": "usb", "port": port, "tool": "usb_live_check"}).start()
        t_cal = time.monotonic()
        cal.wait(timeout=a.seconds / 2)
        time.sleep(max(0.0, a.seconds / 2 - (time.monotonic() - t_cal)))
        if cal.result.done:
            append_history(cal.result)
            if rec is not None:
                d = cal.result.to_dict()
                d.pop("requested")
                rec.note_event("calibration", cal.result.requested, **d)
    else:
        time.sleep(a.seconds)
    s.disconnect()
    s.join(5)

    for t, k, info in events:
        print(f"  [{t - t0:7.3f}s] {k} {info}")
    print(f"포트 {port}, 상태 {s.status} {s.error}")
    print(f"센서 {s.sensor_type.label} (taxel {s.sensor_type.forces}), serviceID {s.service_id}, slot {s.slot}")
    data = [(t, d) for t, d in tr.tx if d[6] == 0xFB and d[7:9] == bytes([0xF0, 0x03])]
    if len(frames) > 1:
        dur = frames[-1].t - frames[0].t
        print(f"프레임 {len(frames)}개, {len(frames) / dur:.1f} Hz, 헤더 오류 {s.header_errors}")
        gaps = [b[0] - a_[0] for a_, b in zip(data, data[1:])]
        q = statistics.quantiles(gaps, n=100)
        print(f"데이터 요청 간격 ms: 중앙값 {statistics.median(gaps) * 1e3:.2f}, "
              f"1% {q[0] * 1e3:.2f}, 99% {q[98] * 1e3:.2f}, 최대 {max(gaps) * 1e3:.2f}")
        f = frames[-1]
        print(f"마지막 합력 raw {f.combine} → N {[v / 10 for v in f.combine]}")
    if cal is not None:
        r = cal.result
        ms = lambda x, y: "-" if x is None or y is None else f"{(y - x) * 1e3:.1f} ms"
        print(f"캘리브레이션: {OUTCOME_TEXT.get(r.outcome, r.outcome)}, status {r.status}, 기능 코드 {r.function_code}")
        print(f"  버튼→전송 {ms(r.requested, r.sent)}, 전송→응답 {ms(r.sent, r.acked)}")
        print(f"  합력 raw 평균 전 {r.before} → 후 {r.after}")
    if rec is not None:
        n_rows = rec.path.read_bytes().count(b"\n") - 1 if rec.path.exists() else 0
        print(f"기록 {rec.path} : 행 {n_rows}, 기록기 {rec.line_count}, 프레임 {len(frames)} "
              f"({'일치' if n_rows == rec.line_count == len(frames) else '불일치'})")
    print("처음 보낸 명령:")
    for t, d in tr.tx[:11]:
        print(f"  {t - t0:7.3f}s {d.hex()}")
    return 0 if s.status != "error" and frames else 1


if __name__ == "__main__":
    raise SystemExit(main())
