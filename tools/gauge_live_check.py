"""Force gauge를 직접 읽어 본다. 센서도 함께 읽으면 같은 시계 기준 지연을 추정한다 (계획 P5 완료 기준).

    python tools/gauge_live_check.py [COM7] [--seconds 10] [--sensor [COM3]] [--sim]

게이지만: 수신 속도·값 범위·최근 값과 수신 간격을 출력한다.
--sensor: USB 센서도 연결해 같은 시간 동안 읽고, 게이지 N ↔ 센서 합력 크기(raw/10)의 상호상관으로 지연을 추정한다.
          누르기·떼기를 몇 번 해야 추정된다 (가만히 두면 "추정 불가"). 센서 포트를 비우면 CH343 포트를 자동 선택.
--sim: 장비 없이 시뮬레이션 게이지(·센서)로 같은 흐름을 돌려 본다.

실장비는 받은 바이트를 그대로 `data/gauge_raw/YYYY-MM-DD-HHMMSS.txt`에 남기고(시각 + 바이트),
0.5 s 넘는 수신 공백과 범위 밖 값(|값| > --max-n)이 있으면 그 주변 바이트를 출력한다 (원인 확인용).
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paxkit.config import Config  # noqa: E402
from paxkit.paths import data_path  # noqa: E402
from paxkit.device.sim import SimUsbTransport  # noqa: E402
from paxkit.device.transport import SerialTransport, default_sensor_port, list_serial_ports  # noqa: E402
from paxkit.device.usb import UsbSensor  # noqa: E402
from paxkit.gauge import SerialGauge, SimGauge, sensor_lag  # noqa: E402
from paxkit.gauge.reader import _open_serial  # noqa: E402

GAP_S = 0.5


class RawLog:
    """시리얼 객체를 감싸 read로 받은 바이트를 시각과 함께 남긴다."""

    def __init__(self, clock):
        self.clock = clock
        self.chunks = []   # (PC 시각, bytes)

    def open(self, port, baud, timeout):
        ser = _open_serial(port, baud, timeout)
        log = self

        class Wrapped:
            def __enter__(self):
                ser.__enter__()
                return self

            def __exit__(self, *a):
                return ser.__exit__(*a)

            def __getattr__(self, name):
                return getattr(ser, name)

            def read(self, n=1):
                b = ser.read(n)
                if b:
                    log.chunks.append((log.clock.wall(), b))
                return b

        return Wrapped()

    def around(self, t0, t1):
        return b"".join(b for t, b in self.chunks if t0 <= t <= t1)

    def save(self, path):
        lines = [f"{t:.4f} {b.hex()} {b!r}" for t, b in self.chunks]
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--sensor", nargs="?", const="", default=None)
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--max-n", type=float, default=600.0, help="이보다 큰 |값|은 범위 밖으로 표시 (ZP-500N)")
    a = ap.parse_args()
    cfg = Config.load()

    sensor = None
    if a.sensor is not None:
        if a.sim:
            transport = SimUsbTransport("S1813E")
        else:
            port = a.sensor or default_sensor_port()
            if not port:
                print("센서 포트를 찾지 못함. 포트 목록:")
                for p in list_serial_ports():
                    print(f"  {p.device}  {p.description}  {'(CH343)' if p.is_sensor else ''}")
                return 1
            transport = SerialTransport(port)
        sensor = UsbSensor(transport)

    raw = None
    if a.sim:
        gauge = SimGauge(load=sensor.transport.load_N if sensor else None)
    else:
        gauge = SerialGauge({**cfg.section("gauge"), "port": a.port or cfg.get("gauge.port", "COM7")})
        raw = RawLog(gauge.clock)
        gauge.open_serial = raw.open
    samples = []
    gauge.add_sink(lambda t, v: samples.append((t, v)))
    gauge.start()
    if sensor is not None:
        sensor.start()
    print(f"게이지 {gauge.port}{', 센서 함께' if sensor else ''}: {a.seconds:.0f}초 동안 읽는 중"
          + (" (누르기·떼기를 몇 번 해 주세요)" if sensor else ""))
    time.sleep(a.seconds)
    now = gauge.clock.wall()
    lag = sensor_lag(gauge.buffer, sensor.buffer, now, window=a.seconds) if sensor else None
    status = f"{gauge.status} {gauge.error}".strip()   # 멈추면 disconnected가 되므로 먼저 읽는다
    gauge.stop()
    gauge.join(3)
    if sensor is not None:
        sensor.disconnect()
        sensor.join(5)

    print(f"게이지 상태 {status}")
    if raw is not None:
        path = data_path("gauge_raw") / (time.strftime("%Y-%m-%d-%H%M%S") + ".txt")
        raw.save(path)
        print(f"받은 바이트 {sum(len(b) for _, b in raw.chunks)}개 → {path}")
    if len(samples) < 2:
        print("게이지 값 없음 (포트·전원·출력 설정·케이블 확인)")
        return 1
    t = np.array([s[0] for s in samples])
    v = np.array([s[1] for s in samples])
    gaps = np.diff(t)
    print(f"게이지 {len(samples)}개, {len(samples) / (t[-1] - t[0]):.1f} Hz, "
          f"값 {v.min():.1f} ~ {v.max():.1f} N, 최근 {v[-1]:.1f} N")
    print(f"수신 간격 ms: 중앙값 {statistics.median(gaps) * 1e3:.1f}, 최소 {gaps.min() * 1e3:.1f}, "
          f"최대 {gaps.max() * 1e3:.1f}")
    t_first = t[0]
    for i in np.flatnonzero(gaps > GAP_S):
        print(f"  수신 공백 {t[i] - t_first:.2f}s → {t[i + 1] - t_first:.2f}s ({gaps[i]:.2f}s), "
              f"전후 값 {v[i]:.1f} → {v[i + 1]:.1f}")
        if raw is not None:
            print(f"    전후 바이트 {raw.around(t[i] - 0.3, t[i + 1] + 0.3)!r}")
    for i in np.flatnonzero(np.abs(v) > a.max_n):
        print(f"  범위 밖 값 {t[i] - t_first:.2f}s: {v[i]:.1f} N")
        if raw is not None:
            print(f"    주변 바이트 {raw.around(t[i] - 0.5, t[i] + 0.3)!r}")
    if sensor is not None:
        print(f"센서 {sensor.sensor_type.label}, 프레임 {sensor.frame_count}, 상태 {sensor.status} {sensor.error}")
        off, r = lag
        if off is None:
            print("지연: 추정 불가 (게이지 값 변화가 0.3 N 미만이거나 데이터 부족)")
        else:
            print(f"지연(센서 - 게이지): {off * 1e3:+.0f} ms, 상관 {r:.3f}"
                  "  (+ 이면 센서가 늦음. 게이지 약 10 Hz라 ±50 ms 정도는 구분이 어려움)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
